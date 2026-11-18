"""Archivos de caso: definición declarativa (JSON) de una decisión estratégica.

Permite usar Oraqlo sin escribir código: la pregunta, las acciones con sus
resultados posibles, las utilidades y el perfil de riesgo viven en un JSON
que la app web (o cualquier frontend) carga y valida.

Formato:
{
  "question": "¿...?",
  "horizon_days": 270,
  "actions": {
    "id-accion": {
      "description": "qué se haría",
      "outcomes": {"resultado": 0.5, "otro": 0.5}
    }
  },
  "utilities": {"resultado": 1.0, "otro": -0.5},
  "risk": {"risk_aversion": 0.3, "ruin_threshold": -0.9, "max_ruin_probability": 0.25},
  "rival": {                                   (opcional: alguien que reacciona)
    "description": "la competencia",
    "temperature": 0.3,                        (bajo = responde muy bien; alto = errático)
    "responses": {"id-respuesta": {"description": "qué haría"}},
    "reactions": {
      "id-accion": {"id-respuesta": {"their_utility": 0.4, "outcomes": {"resultado": 1.0}}}
    }
  }
}

Con `rival`, el resultado de cada acción depende de cómo responda el otro, que
elige su respuesta según SU utilidad (`SoftmaxAdversary`): nunca se le supone pasivo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from oraqlo.forecaster.base import Distribution
from oraqlo.simulator.base import (
    Action,
    OutcomeModel,
    SoftmaxAdversary,
    UtilityFn,
    with_adversary,
)
from oraqlo.strategy.planner import RiskProfile


class CaseError(ValueError):
    """El archivo de caso es inválido; el mensaje dice exactamente qué corregir."""


@dataclass
class Rival:
    """Alguien que reacciona a tu jugada buscando lo suyo (competencia, contraparte…)."""

    description: str
    responses: list[Action]
    # tu acción → su respuesta → (su utilidad, distribución de resultados para ti)
    reactions: dict[str, dict[str, tuple[float, dict[str, float]]]]
    temperature: float = 0.3

    def adversary(self) -> SoftmaxAdversary:
        return SoftmaxAdversary(
            self.responses,
            lambda state, ours, theirs: self.reactions[ours.id][theirs.id][0],
            temperature=self.temperature,
        )

    def outcome_model(self) -> OutcomeModel:
        return with_adversary(
            self.adversary(),
            lambda state, ours, theirs: Distribution(
                outcomes=dict(self.reactions[ours.id][theirs.id][1])
            ),
        )

    def likely_responses(self, action: Action) -> list[tuple[Action, float]]:
        from oraqlo.world_model import WorldModel

        return self.adversary().respond(WorldModel().current(), action)


@dataclass
class CaseSpec:
    question: str
    horizon: timedelta
    actions: list[Action]
    outcomes: dict[str, dict[str, float]]  # id de acción → {resultado: prob}
    utilities: dict[str, float]  # resultado → utilidad
    risk: RiskProfile = field(default_factory=RiskProfile)
    depth: int = 1
    rival: Rival | None = None

    def outcome_model(self) -> OutcomeModel:
        if self.rival is not None:
            return self.rival.outcome_model()
        return lambda state, action: Distribution(outcomes=self.outcomes[action.id])

    def utility_fn(self) -> UtilityFn:
        return lambda state, path: self.utilities[path[-1]]


def parse_case(data: dict) -> CaseSpec:
    """Valida y construye un CaseSpec. Todos los errores son accionables."""
    question = data.get("question")
    if not question or not str(question).strip():
        raise CaseError('Falta "question": la pregunta estratégica a decidir.')

    horizon_days = data.get("horizon_days")
    if not isinstance(horizon_days, (int, float)) or horizon_days <= 0:
        raise CaseError('"horizon_days" debe ser un número de días > 0.')

    raw_actions = data.get("actions")
    if not isinstance(raw_actions, dict) or not raw_actions:
        raise CaseError('"actions" debe ser un objeto con al menos una acción.')

    utilities = data.get("utilities")
    if not isinstance(utilities, dict) or not utilities:
        raise CaseError('"utilities" debe mapear cada resultado a su utilidad numérica.')
    utilities = {str(k): float(v) for k, v in utilities.items()}

    actions: list[Action] = []
    outcomes: dict[str, dict[str, float]] = {}
    for action_id, spec in raw_actions.items():
        if not isinstance(spec, dict):
            raise CaseError(f'La acción "{action_id}" debe ser un objeto.')
        description = spec.get("description")
        if not description:
            raise CaseError(f'La acción "{action_id}" necesita "description".')
        action_outcomes = spec.get("outcomes")
        if not isinstance(action_outcomes, dict) or not action_outcomes:
            raise CaseError(
                f'La acción "{action_id}" necesita "outcomes" (resultado → probabilidad).'
            )
        probs = {str(k): float(v) for k, v in action_outcomes.items()}
        if any(not 0.0 <= p <= 1.0 for p in probs.values()):
            raise CaseError(f'Las probabilidades de "{action_id}" deben estar entre 0 y 1.')
        total = sum(probs.values())
        if not 0.99 <= total <= 1.01:
            raise CaseError(
                f'Las probabilidades de "{action_id}" suman {total:.3f}; deben sumar 1.0.'
            )
        missing = [label for label in probs if label not in utilities]
        if missing:
            raise CaseError(
                f'Resultados de "{action_id}" sin utilidad definida en "utilities": {missing}.'
            )
        actions.append(Action(id=str(action_id), description=str(description)))
        outcomes[str(action_id)] = probs

    risk_data = data.get("risk", {})
    if not isinstance(risk_data, dict):
        raise CaseError('"risk" debe ser un objeto (o omitirse para usar los defaults).')
    try:
        risk = RiskProfile(
            risk_aversion=float(risk_data.get("risk_aversion", 0.3)),
            cvar_alpha=float(risk_data.get("cvar_alpha", 0.05)),
            ruin_threshold=(
                float(risk_data["ruin_threshold"])
                if risk_data.get("ruin_threshold") is not None
                else None
            ),
            max_ruin_probability=float(risk_data.get("max_ruin_probability", 0.01)),
        )
    except (TypeError, ValueError) as e:
        raise CaseError(f'Valores inválidos en "risk": {e}') from e

    rival = _parse_rival(data.get("rival"), [a.id for a in actions], utilities)

    return CaseSpec(
        question=str(question),
        horizon=timedelta(days=horizon_days),
        actions=actions,
        outcomes=outcomes,
        utilities=utilities,
        risk=risk,
        depth=int(data.get("depth", 1)),
        rival=rival,
    )


def _parse_rival(raw, action_ids: list[str], utilities: dict[str, float]) -> Rival | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CaseError('"rival" debe ser un objeto (o omitirse).')
    responses_raw = raw.get("responses")
    if not isinstance(responses_raw, dict) or len(responses_raw) < 2:
        raise CaseError('"rival.responses" necesita al menos dos respuestas posibles del rival.')
    responses = []
    for rid, spec in responses_raw.items():
        text = spec.get("description") if isinstance(spec, dict) else None
        if not text:
            raise CaseError(f'La respuesta del rival "{rid}" necesita "description".')
        responses.append(Action(id=str(rid), description=str(text)))
    reactions_raw = raw.get("reactions")
    if not isinstance(reactions_raw, dict):
        raise CaseError(
            '"rival.reactions" debe dar, por cada acción tuya, cada respuesta del rival.'
        )
    reactions: dict[str, dict[str, tuple[float, dict[str, float]]]] = {}
    for action_id in action_ids:
        per = reactions_raw.get(action_id)
        if not isinstance(per, dict):
            raise CaseError(f'Falta "rival.reactions.{action_id}".')
        reactions[action_id] = {}
        for r in responses:
            entry = per.get(r.id)
            if not isinstance(entry, dict):
                raise CaseError(f'Falta "rival.reactions.{action_id}.{r.id}".')
            where = f'"rival.reactions.{action_id}.{r.id}"'
            try:
                theirs = float(entry["their_utility"])
                probs = {str(k): float(v) for k, v in (entry.get("outcomes") or {}).items()}
            except (KeyError, TypeError, ValueError) as e:
                raise CaseError(f"Valores inválidos en {where}: {e}") from e
            if any(not 0.0 <= p <= 1.0 for p in probs.values()):
                raise CaseError(f"Las probabilidades de {where} deben estar entre 0 y 1.")
            total = sum(probs.values())
            if not probs or not 0.99 <= total <= 1.01:
                raise CaseError(f"Las probabilidades de {where} deben sumar 1.0.")
            missing = [label for label in probs if label not in utilities]
            if missing:
                raise CaseError(f'Resultados del rival sin utilidad en "utilities": {missing}.')
            reactions[action_id][r.id] = (theirs, {k: v / total for k, v in probs.items()})
    try:
        temperature = float(raw.get("temperature", 0.3))
    except (TypeError, ValueError) as e:
        raise CaseError('"rival.temperature" debe ser un número > 0.') from e
    if temperature <= 0:
        raise CaseError('"rival.temperature" debe ser un número > 0.')
    return Rival(
        description=str(raw.get("description") or "el rival"),
        responses=responses,
        reactions=reactions,
        temperature=temperature,
    )


def case_to_dict(spec: CaseSpec) -> dict:
    """Inverso de parse_case: serializa un CaseSpec al formato del archivo de caso."""
    descriptions = {a.id: a.description for a in spec.actions}
    data: dict = {
        "question": spec.question,
        "horizon_days": spec.horizon.days,
        "actions": {
            action_id: {"description": descriptions[action_id], "outcomes": dict(probs)}
            for action_id, probs in spec.outcomes.items()
        },
        "utilities": dict(spec.utilities),
        "risk": {
            "risk_aversion": spec.risk.risk_aversion,
            "cvar_alpha": spec.risk.cvar_alpha,
            "ruin_threshold": spec.risk.ruin_threshold,
            "max_ruin_probability": spec.risk.max_ruin_probability,
        },
    }
    if spec.depth != 1:
        data["depth"] = spec.depth
    if spec.rival is not None:
        data["rival"] = {
            "description": spec.rival.description,
            "temperature": spec.rival.temperature,
            "responses": {r.id: {"description": r.description} for r in spec.rival.responses},
            "reactions": {
                aid: {
                    rid: {"their_utility": theirs, "outcomes": dict(probs)}
                    for rid, (theirs, probs) in per.items()
                }
                for aid, per in spec.rival.reactions.items()
            },
        }
    return data


def load_case(path: str | Path) -> CaseSpec:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CaseError(f"JSON inválido en {path}: {e}") from e
    return parse_case(data)


TEMPLATE = {
    "question": "¿Qué decisión estratégica quieres evaluar?",
    "horizon_days": 180,
    "actions": {
        "opcion-a": {
            "description": "Descripción concreta de la jugada A",
            "outcomes": {"sale bien": 0.5, "resultado mediocre": 0.3, "sale mal": 0.2},
        },
        "opcion-b": {
            "description": "Descripción concreta de la jugada B",
            "outcomes": {"sale bien": 0.2, "resultado mediocre": 0.6, "sale mal": 0.2},
        },
    },
    "utilities": {"sale bien": 1.0, "resultado mediocre": 0.0, "sale mal": -0.8},
    "risk": {"risk_aversion": 0.3, "ruin_threshold": -0.9, "max_ruin_probability": 0.25},
}


def template_json() -> str:
    return json.dumps(TEMPLATE, ensure_ascii=False, indent=2)
