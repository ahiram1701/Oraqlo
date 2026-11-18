"""LLMForecaster: el oráculo cualitativo — razonamiento LLM vía Ollama.

Cubre las preguntas donde no hay datos para un modelo cuantitativo: pide al LLM
escenarios con probabilidades en JSON estricto, los valida y normaliza, y emite
un Forecast auditable (con supuestos y fuente) listo para el CalibrationLedger.

Para ser más fiable que una sola respuesta del modelo:
- parte de una tasa base (vista exterior) antes de ajustar con lo específico;
- pide varias estimaciones sobre los mismos escenarios, cada una con un enfoque
  distinto y, si hay comité, a modelos distintos (forecaster.committee), y las combina;
- nunca emite 0% ni 100% (forecaster.aggregate.clamp);
- aplica la corrección aprendida del historial del modelo (memory.recalibration).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from oraqlo.forecaster import numeric
from oraqlo.forecaster.aggregate import (
    NO_DATA_BASE_NOTE,
    NO_DATA_NOTE,
    clamp,
    parse_base_rate,
    pool_log_odds,
    temper_without_data,
)
from oraqlo.forecaster.base import Distribution, Forecast, Forecaster
from oraqlo.forecaster.committee import (
    LENSES,
    STRUCTURE_VOICE,
    Member,
    build_members,
    combine,
    run_members,
    weights_note,
)
from oraqlo.llm.base import ChatMessage, LLMProvider
from oraqlo.llm.structured import extract_json_object
from oraqlo.world_model import WorldState

if TYPE_CHECKING:
    from oraqlo.forecaster.markets import MarketFinder
    from oraqlo.memory.recalibration import Recalibration

_SYSTEM_PROMPT = (
    "Eres el Oráculo de Oraqlo: emites probabilidades calibradas, nunca certezas. "
    "Responde EXCLUSIVAMENTE con un objeto JSON con esta forma exacta:\n"
    '{"base_rate": "<clase de referencia y con qué frecuencia ocurre>", '
    '"base_rate_outcomes": {"<escenario>": <prob según solo la tasa base>, ...}, '
    '"outcomes": {"<escenario>": <prob>, ...}, "assumptions": ["<supuesto>", ...], '
    '"rationale": "<una frase>"}\n'
    "Método: empieza por la TASA BASE (la vista exterior: en casos parecidos, con qué "
    "frecuencia pasa cada cosa; ponla en cifras en base_rate_outcomes, con los mismos "
    "escenarios) y solo después ajústala con lo específico de esta pregunta; evita 0 y 1 "
    "salvo imposibilidad lógica. Si la pregunta es «¿pasará X antes de una fecha?», "
    "piensa en el ritmo al que suelen pasar estas cosas y aplícalo a los días que quedan: "
    "lo más frecuente es que nada cambie.\n"
    "Reglas: entre 2 y 5 escenarios mutuamente excluyentes y exhaustivos; las "
    "probabilidades deben sumar 1.0; a mayor horizonte temporal, reparte más la "
    "probabilidad (más incertidumbre); si no tienes datos concretos de ESTE caso (ranking, "
    "forma, cuotas, encuestas, cifras recientes), quédate en la tasa base o cerca del "
    "reparto igual: una ventaja que solo intuyes no pasa del 55-60%. Los supuestos son las "
    "condiciones de las que depende tu estimación; escribe escenarios y supuestos en el "
    "idioma de la pregunta.\n"
    "Si la pregunta pide una CANTIDAD (cuánto, cuántos, qué cifra, qué precio), los "
    "escenarios son INTERVALOS contiguos que lo cubren todo, de menor a mayor («menos de "
    "10», «de 10 a 20», «20 o más»), y añade "
    '"numeric": {"unit": "<unidad>", "bins": [[null, 10], [10, 20], [20, null]]} con un '
    "par [desde, hasta) por escenario, en el mismo orden."
)

_FIXED_LABELS = (
    "Estima de forma independiente. Usa EXACTAMENTE estos escenarios, con estas mismas "
    "etiquetas (ni uno más, ni uno menos): {labels}"
)


@dataclass
class Parsed:
    outcomes: dict[str, float]
    assumptions: list[str]
    rationale: str
    base_rate: str
    base_rate_outcomes: object = None
    numeric: object = None


class ForecastParseError(ValueError):
    """El LLM no produjo un JSON de predicción utilizable tras los reintentos."""


def _parse_forecast_json(text: str) -> Parsed:
    """Extrae escenarios, supuestos, razonamiento y tasa base del texto del modelo.

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

    return Parsed(
        outcomes=outcomes,
        assumptions=[str(a) for a in data.get("assumptions", [])],
        rationale=str(data.get("rationale", "")),
        base_rate=str(data.get("base_rate") or "").strip(),
        base_rate_outcomes=data.get("base_rate_outcomes"),
        numeric=data.get("numeric"),
    )


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


def _match_labels(outcomes: dict[str, float], labels: list[str]) -> dict[str, float] | None:
    """La misma distribución con las etiquetas fijadas (None si cambió los escenarios)."""
    by_norm = {_norm(k): v for k, v in outcomes.items()}
    if set(by_norm) != {_norm(k) for k in labels}:
        return None
    matched = {k: by_norm[_norm(k)] for k in labels}
    total = sum(matched.values())
    return {k: v / total for k, v in matched.items()} if total > 0 else None


class LLMForecaster(Forecaster):
    """Forecaster cualitativo respaldado por uno o varios modelos (un comité).

    Sin `members`, pregunta `samples` veces al modelo del rol, cada vez con un enfoque
    distinto. Con `members` (ver forecaster.committee), cada miembro es un modelo con su
    enfoque y se combinan con `weights` (por modelo)."""

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
        members: list[Member] | None = None,
        weights: dict[str, float] | None = None,
        market_finder: MarketFinder | None = None,
        decompose: bool = False,
        reference: Callable[[str], str] | None = None,
        weigh: Callable[[list[str]], dict[str, float]] | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.source = source or f"llm:{model}"
        self.max_retries = max_retries
        # Consejo de calibración derivado del historial resuelto (autopractice).
        self.advice = advice
        self.samples = max(1, int(samples))
        # Corrección numérica aprendida del historial (del modelo o del comité).
        self.recalibration = recalibration
        self.on_progress = on_progress or (lambda msg: None)
        self.members = members or build_members((provider, model), [], self.samples)
        self.weights = weights
        # Voces que no son un modelo con un enfoque (ver forecaster.markets y .decompose).
        self.market_finder = market_finder
        self.decompose = decompose
        # Preguntas parecidas ya resueltas (memory.reference): para la visión externa.
        self.reference = reference
        # Pesos de las voces que no traía `weights` (el mercado, la estructura…).
        self.weigh = weigh
        # Lo que dijo cada voz en la última previsión (para registrarlo aparte).
        self.last_members: dict[str, dict[str, float]] = {}
        # Detalle de las voces extra de la última previsión (mercado, partes).
        self.last_extras: dict[str, dict] = {}

    def _ask(
        self, messages: list[ChatMessage], temperature: float, member: Member | None = None
    ) -> Parsed:
        """Una respuesta válida del modelo, reintentando con el error como feedback."""
        provider = member.provider if member else self.provider
        model = member.model if member else self.model
        messages = list(messages)
        last_error: Exception | None = None
        for _attempt in range(self.max_retries + 1):
            response = provider.chat(
                messages=messages,
                model=model,
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
            f"El modelo {model} no produjo JSON válido tras "
            f"{self.max_retries + 1} intentos: {last_error}"
        )

    def _messages(
        self,
        system: str,
        user: str,
        member: Member,
        labels: list[str] | None,
        reference: str = "",
    ) -> list[ChatMessage]:
        content = f"{user}\n\n{member.lens}"
        if reference and member.lens == LENSES[0]:  # solo la visión externa: errores independientes
            content += f"\n\n{reference}"
        if labels is not None:
            content += "\n\n" + _FIXED_LABELS.format(labels=json.dumps(labels, ensure_ascii=False))
        return [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=content),
        ]

    def _extra_voices(
        self, question: str, horizon: timedelta, labels: list[str], context: str
    ) -> list[tuple[str, dict[str, float]]]:
        """El mercado y la estimación por partes, si aplican. Nunca rompen la previsión:
        si algo falla, se sigue sin esa voz (y se avisa)."""
        draws: list[tuple[str, dict[str, float]]] = []
        if self.market_finder is not None:
            self.on_progress("Buscando un mercado de predicción sobre lo mismo…")
            try:
                voice = self.market_finder.find(question, horizon, labels)
            except Exception as e:
                voice = None
                self.on_progress(f"No pude consultar los mercados ({e}); sigo sin ellos")
            if voice is not None:
                self.on_progress(f"Mercado encontrado en {voice.quote.platform}: se suma al comité")
                draws.append((voice.voice, voice.outcomes))
                self.last_extras["market"] = voice.to_dict()
                self.last_extras["market"]["note"] = voice.note()
        if self.decompose and len(labels) == 2:
            from oraqlo.forecaster.decompose import decompose

            lead = (self.members[0].provider, self.members[0].model)
            other = next((m for m in self.members if m.model != lead[1]), self.members[0])
            try:
                parts = decompose(
                    question, horizon, labels, lead, (other.provider, other.model), context
                )
            except Exception as e:
                parts = None
                self.on_progress(f"No pude estimar por partes ({e}); sigo sin ello")
            if parts is not None:
                self.on_progress(parts.note())
                draws.append((STRUCTURE_VOICE, parts.outcomes(labels)))
                self.last_extras["structure"] = {**parts.to_dict(), "note": parts.note()}
        return draws

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
        reference = ""
        if self.reference is not None:
            try:
                reference = self.reference(question)
            except Exception:  # el historial es una ayuda, no un requisito
                reference = ""
        self.last_extras = {}

        total = len(self.members)
        lead, rest = self.members[0], self.members[1:]
        if total > 1:
            self.on_progress(f"Estimación 1 de {total}")
        # El primero elige los escenarios; los demás estiman sobre esos mismos.
        first = self._ask(
            self._messages(system_prompt, user_prompt, lead, None, reference),
            self.temperature,
            lead,
        )
        labels = list(first.outcomes)
        numeric_spec = first.numeric if isinstance(first.numeric, dict) else {}
        bins = numeric.parse_bins(numeric_spec.get("bins"), labels) if numeric_spec else None
        answers: list[tuple[Member, Parsed]] = [(lead, first)]
        done = [1]

        def ask(member: Member) -> Parsed:
            parsed = self._ask(
                self._messages(system_prompt, user_prompt, member, labels, reference),
                self.temperature,
                member,
            )
            done[0] += 1
            self.on_progress(f"Estimación {done[0]} de {total}")
            return parsed

        answers += run_members(
            rest,
            ask,
            on_error=lambda m, e: self.on_progress(f"{m.model} no respondió ({e}); sigo sin él"),
        )
        draws: list[tuple[str, dict[str, float]]] = []
        bases: list[dict[str, float]] = []
        for member, parsed in answers:
            matched = _match_labels(parsed.outcomes, labels)
            if matched is None:
                continue  # cambió los escenarios: no se puede combinar
            draws.append((member.model, matched))
            if (base := parse_base_rate(parsed.base_rate_outcomes, labels)) is not None:
                bases.append(base)

        extra = self._extra_voices(question, horizon, labels, _summarize_state(state))
        weights = dict(self.weights or {})
        if extra and self.weigh is not None:
            names = list(dict.fromkeys([*(m for m, _ in draws), *(v for v, _ in extra)]))
            missing = [n for n in names if n not in weights]
            if missing:
                weights.update(self.weigh(missing))
        outcomes, per_model = combine([*draws, *extra], weights or None)
        self.last_members = per_model if len(per_model) > 1 else {}
        notes: list[str] = []
        has_market = "market" in self.last_extras
        if not state.entities and not has_market:  # nada investigado ni observado: intuición
            base = pool_log_odds(bases) if bases else None
            outcomes = temper_without_data(outcomes, base)
            notes.append(NO_DATA_BASE_NOTE if base else NO_DATA_NOTE)
        if first.base_rate:
            notes.append(f"Tasa base: {first.base_rate}")
        if first.rationale:
            notes.append(f"Razonamiento: {first.rationale}")
        if reference:
            notes.append("Se tuvieron en cuenta preguntas parecidas de tu historial.")
        for key in ("market", "structure"):
            if key in self.last_extras:
                notes.append(self.last_extras[key]["note"])
        if len(per_model) > 1:
            notes.append(weights_note(weights, per_model))
        elif len(draws) > 1:
            notes.append(f"Agregado de {len(draws)} estimaciones independientes del modelo.")
        if self.recalibration is not None:
            from oraqlo.memory.recalibration import group_key
            from oraqlo.memory.scorecard import forecast_kind

            group = group_key(forecast_kind(self.source, question), len(labels), horizon.days)
            if self.recalibration.active_for(group):
                outcomes = clamp(self.recalibration.apply(outcomes, group))
                notes.append(self.recalibration.note(group))
        if bins is not None:
            notes.append(numeric.note(str(numeric_spec.get("unit") or ""), bins))

        distribution = Distribution(outcomes=outcomes)
        distribution.validate()

        return Forecast(
            id=str(uuid.uuid4()),
            question=question,
            distribution=distribution,
            horizon=horizon,
            assumptions=[*first.assumptions, *notes],
            source=self.source,
            created_at=datetime.now(UTC),
        )
