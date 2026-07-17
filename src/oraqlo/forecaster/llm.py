"""LLMForecaster: el oráculo cualitativo — razonamiento LLM vía Ollama.

Cubre las preguntas donde no hay datos para un modelo cuantitativo: pide al LLM
escenarios con probabilidades en JSON estricto, los valida y normaliza, y emite
un Forecast auditable (con supuestos y fuente) listo para el CalibrationLedger.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

from oraqlo.forecaster.base import Distribution, Forecast, Forecaster
from oraqlo.llm.base import ChatMessage, LLMProvider
from oraqlo.world_model import WorldState

_SYSTEM_PROMPT = (
    "Eres el Oráculo de Oraqlo: emites probabilidades calibradas, nunca certezas. "
    "Responde EXCLUSIVAMENTE con un objeto JSON con esta forma exacta:\n"
    '{"outcomes": {"<escenario>": <prob>, ...}, "assumptions": ["<supuesto>", ...], '
    '"rationale": "<una frase>"}\n'
    "Reglas: entre 2 y 5 escenarios mutuamente excluyentes y exhaustivos; las "
    "probabilidades deben sumar 1.0; a mayor horizonte temporal, reparte más la "
    "probabilidad (más incertidumbre); los supuestos son las condiciones de las "
    "que depende tu estimación; escribe escenarios y supuestos en el idioma de "
    "la pregunta."
)


class ForecastParseError(ValueError):
    """El LLM no produjo un JSON de predicción utilizable tras los reintentos."""


def _parse_forecast_json(text: str) -> tuple[dict[str, float], list[str], str]:
    """Extrae (outcomes, assumptions, rationale) del texto del modelo.

    Tolera texto alrededor del JSON (busca el primer '{' y el último '}'),
    pero es estricto con la estructura interna.
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ForecastParseError(f"Sin objeto JSON en la respuesta: {text[:200]!r}")
    data = json.loads(text[start : end + 1])

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
    return outcomes, assumptions, rationale


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
    ) -> None:
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.source = source or f"llm:{model}"
        self.max_retries = max_retries
        # Consejo de calibración derivado del historial resuelto (autopractice):
        # el mecanismo por el que el oráculo aprende de sus errores pasados.
        self.advice = advice

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

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            response = self.provider.chat(
                messages=messages,
                model=self.model,
                temperature=self.temperature,
                json_mode=True,
            )
            try:
                outcomes, assumptions, rationale = _parse_forecast_json(response.content)
                break
            except (ForecastParseError, json.JSONDecodeError, TypeError, ValueError) as e:
                last_error = e
                # Reintento con el error como feedback: los modelos corrigen bien
                # su propio JSON cuando se les muestra qué falló.
                messages.append(ChatMessage(role="assistant", content=response.content))
                messages.append(
                    ChatMessage(
                        role="user",
                        content=f"Tu respuesta no es válida ({e}). Devuelve SOLO el JSON corregido.",
                    )
                )
        else:
            raise ForecastParseError(
                f"El modelo {self.model} no produjo JSON válido tras "
                f"{self.max_retries + 1} intentos: {last_error}"
            )

        distribution = Distribution(outcomes=outcomes)
        distribution.validate()
        if rationale:
            assumptions = [*assumptions, f"Razonamiento: {rationale}"]

        return Forecast(
            id=str(uuid.uuid4()),
            question=question,
            distribution=distribution,
            horizon=horizon,
            assumptions=assumptions,
            source=self.source,
            created_at=datetime.now(timezone.utc),
        )
