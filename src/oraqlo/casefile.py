"""Archivos de caso: definición declarativa (JSON) de una decisión estratégica.

Permite usar Oraqlo sin escribir código: la pregunta, las acciones con sus
resultados posibles, las utilidades y el perfil de riesgo viven en un JSON
que la TUI (o cualquier frontend) carga y valida.

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
  "risk": {"risk_aversion": 0.3, "ruin_threshold": -0.9, "max_ruin_probability": 0.25}
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from oraqlo.forecaster.base import Distribution
from oraqlo.simulator.base import Action, OutcomeModel, UtilityFn
from oraqlo.strategy.planner import RiskProfile


class CaseError(ValueError):
    """El archivo de caso es inválido; el mensaje dice exactamente qué corregir."""


@dataclass
class CaseSpec:
    question: str
    horizon: timedelta
    actions: list[Action]
    outcomes: dict[str, dict[str, float]]  # id de acción → {resultado: prob}
    utilities: dict[str, float]  # resultado → utilidad
    risk: RiskProfile = field(default_factory=RiskProfile)
    depth: int = 1

    def outcome_model(self) -> OutcomeModel:
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

    return CaseSpec(
        question=str(question),
        horizon=timedelta(days=horizon_days),
        actions=actions,
        outcomes=outcomes,
        utilities=utilities,
        risk=risk,
        depth=int(data.get("depth", 1)),
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
