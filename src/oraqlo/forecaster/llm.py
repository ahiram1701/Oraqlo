"""LLMForecaster: el oráculo cualitativo — razonamiento LLM vía Ollama.

Cubre las preguntas donde no hay datos para un modelo cuantitativo: pide al LLM
escenarios con probabilidades en JSON estricto, los valida y normaliza, y emite
un Forecast auditable (con supuestos y fuente) listo para el CalibrationLedger.

Para ser más fiable que una sola respuesta del modelo:
- parte de una tasa base (vista exterior) antes de ajustar con lo específico;
- puede pedir varias estimaciones sobre los mismos escenarios y agregarlas;
- nunca emite 0% ni 100% (forecaster.aggregate.clamp);
- aplica la corrección aprendida del historial del modelo (memory.recalibration).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from oraqlo.forecaster.aggregate import clamp, pool_log_odds
from oraqlo.forecaster.base import Distribution, Forecast, Forecaster
from oraqlo.llm.base import ChatMessage, LLMProvider
from oraqlo.llm.structured import extract_json_object
from oraqlo.world_model import WorldState

if TYPE_CHECKING:
    from oraqlo.memory.recalibration import Recalibration

_SYSTEM_PROMPT = (
    "Eres el Oráculo de Oraqlo: emites probabilidades calibradas, nunca certezas. "
    "Responde EXCLUSIVAMENTE con un objeto JSON con esta forma exacta:\n"
    '{"base_rate": "<clase de referencia y con qué frecuencia ocurre>", '
    '"outcomes": {"<escenario>": <prob>, ...}, "assumptions": ["<supuesto>", ...], '
    '"rationale": "<una frase>"}\n'
    "Método: empieza por la TASA BASE (la vista exterior: en casos parecidos, con qué "
    "frecuencia pasa cada cosa) y solo después ajústala con lo específico de esta "
    "pregunta; evita 0 y 1 salvo imposibilidad lógica.\n"
    "Reglas: entre 2 y 5 escenarios mutuamente excluyentes y exhaustivos; las "
    "probabilidades deben sumar 1.0; a mayor horizonte temporal, reparte más la "
    "probabilidad (más incertidumbre); los supuestos son las condiciones de las "
    "que depende tu estimación; escribe escenarios y supuestos en el idioma de "
    "la pregunta."
)

_FIXED_LABELS = (
    "Vuelve a estimar desde cero, de forma independiente. Usa EXACTAMENTE estos "
    "escenarios, con estas mismas etiquetas (ni uno más, ni uno menos): {labels}"
)

# Las muestras extra necesitan algo de variedad para que agregarlas aporte.
SAMPLE_TEMPERATURE = 0.7

Parsed = tuple[dict[str, float], list[str], str, str]


class ForecastParseError(ValueError):
    """El LLM no produjo un JSON de predicción utilizable tras los reintentos."""


def _parse_forecast_json(text: str) -> Parsed:
    """Extrae (outcomes, assumptions, rationale, base_rate) del texto del modelo.

    Tolera texto alrededor del JSON (busca el primer '{' y el último '}'),
    pero es estricto con la estructura interna.
    """
    try:
        data = extract_json_object(text)
    except ValueError as e:
        raise ForecastParseError(str(e)) from e

    raw_outcomes = data.get("outcomes")
    if not isinstance(raw_outcomes, dict) or not raw_outcomes:
        raise ForecastParseError("Falta 'outcomes' o está vacío")
    outcomes = {str(k): float(v) for k, v in raw_outcomes.items()}
    if any(p < 0.0 for p in outcomes.values()):
        raise ForecastParseError(f"Probabilidad negativa: {outcomes}")

    total = sum(outcomes.values())
    if total <= 0.0:
        raise ForecastParseError("Todas las probabilidades son cero")
    if not 0.9 <= total <= 1.1:
        raise ForecastParseError(f"Las probabilidades suman {total:.3f}, lejos de 1.0")
    # Normalización exacta: perdonamos redondeos del modelo, no incoherencias.
    outcomes = {k: p / total for k, p in outcomes.items()}

    assumptions = [str(a) for a in data.get("assumptions", [])]
    rationale = str(data.get("rationale", ""))
    base_rate = str(data.get("base_rate") or "").strip()
    return outcomes, assumptions, rationale, base_rate


def _norm(label: str) -> str:
    return " ".join(label.split()).casefold()


def _summarize_state(state: WorldState, max_vars: int = 30) -> str:
    """Contexto compacto del world model para el prompt (o aviso de que está vacío)."""
    lines: list[str] = []
    for entity in state.entities.values():
        for var in entity.variables.values():
            lines.append(
                f"- {entity.id}.{var.name} = {var.value} "
                f"[{var.epistemic.value}, fuente: {var.source or 'n/d'}]"
            )
            if len(lines) >= max_vars:
                break
        if len(lines) >= max_vars:
            break
    if not lines:
        return "(world model vacío: razona solo con la pregunta y conocimiento general)"
    return "\n".join(lines)


class LLMForecaster(Forecaster):
    """Forecaster cualitativo respaldado por un LLMProvider (Ollama local o cloud)."""

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        temperature: float = 0.2,
        source: str | None = None,
        max_retries: int = 1,
        advice: str | None = None,
        samples: int = 1,
        recalibration: Recalibration | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.source = source or f"llm:{model}"
        self.max_retries = max_retries
        # Consejo de calibración derivado del historial resuelto (autopractice):
        # el mecanismo por el que el oráculo aprende de sus errores pasados.
        self.advice = advice
        # Varias estimaciones independientes sobre los mismos escenarios, agregadas
        # en log-odds: reduce el ruido de una sola respuesta del modelo.
        self.samples = max(1, int(samples))
        # Corrección numérica aprendida del Brier histórico de este modelo.
        self.recalibration = recalibration
        self.on_progress = on_progress or (lambda msg: None)

    def _ask(self, messages: list[ChatMessage], temperature: float) -> Parsed:
        """Una respuesta válida del modelo, reintentando con el error como feedback."""
        messages = list(messages)
        last_error: Exception | None = None
        for _attempt in range(self.max_retries + 1):
            response = self.provider.chat(
                messages=messages,
                model=self.model,
                temperature=temperature,
                json_mode=True,
            )
            try:
                return _parse_forecast_json(response.content)
            except (ForecastParseError, json.JSONDecodeError, TypeError, ValueError) as e:
                last_error = e
                # Los modelos corrigen bien su propio JSON cuando se les muestra qué falló.
                messages.append(ChatMessage(role="assistant", content=response.content))
                messages.append(
                    ChatMessage(
                        role="user",
                        content=f"Tu respuesta no es válida ({e}). Devuelve SOLO el JSON corregido.",
                    )
                )
        raise ForecastParseError(
            f"El modelo {self.model} no produjo JSON válido tras "
            f"{self.max_retries + 1} intentos: {last_error}"
        )

    def _extra_sample(
        self, messages: list[ChatMessage], labels: list[str]
    ) -> dict[str, float] | None:
        """Otra estimación sobre los escenarios ya fijados (None si no es comparable).

        Una muestra extra que falla no tumba la previsión: ya hay al menos una.
        """
        fixed = [
            *messages,
            ChatMessage(role="user", content=_FIXED_LABELS.format(labels=json.dumps(labels))),
        ]
        try:
            outcomes, *_ = self._ask(fixed, max(self.temperature, SAMPLE_TEMPERATURE))
        except Exception:
            return None
        by_norm = {_norm(k): v for k, v in outcomes.items()}
        if set(by_norm) != {_norm(k) for k in labels}:
            return None  # cambió los escenarios: no se puede agregar
        matched = {k: by_norm[_norm(k)] for k in labels}
        total = sum(matched.values())
        return {k: v / total for k, v in matched.items()} if total > 0 else None

    def forecast(self, state: WorldState, question: str, horizon: timedelta) -> Forecast:
        user_prompt = (
            f"Fecha de hoy: {date.today().isoformat()}\n"
            f"Pregunta: {question}\n"
            f"Horizonte: {horizon.days} días\n"
            f"Estado conocido del mundo:\n{_summarize_state(state)}"
        )
        system_prompt = _SYSTEM_PROMPT
        if self.advice:
            system_prompt += f"\n\nTu historial de calibración dice: {self.advice}"
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_prompt),
        ]

        if self.samples > 1:
            self.on_progress(f"Estimación 1 de {self.samples}")
        outcomes, assumptions, rationale, base_rate = self._ask(messages, self.temperature)
        labels = list(outcomes)
        draws = [outcomes]
        for k in range(2, self.samples + 1):
            self.on_progress(f"Estimación {k} de {self.samples}")
            extra = self._extra_sample(messages, labels)
            if extra is not None:
                draws.append(extra)

        outcomes = clamp(pool_log_odds(draws))
        notes: list[str] = []
        if base_rate:
            notes.append(f"Tasa base: {base_rate}")
        if rationale:
            notes.append(f"Razonamiento: {rationale}")
        if len(draws) > 1:
            notes.append(f"Agregado de {len(draws)} estimaciones independientes del modelo.")
        if self.recalibration is not None and self.recalibration.active:
            outcomes = clamp(self.recalibration.apply(outcomes))
            notes.append(self.recalibration.note())

        distribution = Distribution(outcomes=outcomes)
        distribution.validate()

        return Forecast(
            id=str(uuid.uuid4()),
            question=question,
            distribution=distribution,
            horizon=horizon,
            assumptions=[*assumptions, *notes],
            source=self.source,
            created_at=datetime.now(UTC),
        )
