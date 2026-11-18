"""¿Qué haría cambiar la recomendación? ¿Qué conviene averiguar antes de decidir?

Una recomendación sin sus puntos de ruptura es frágil: no sabes cuánto puedes
equivocarte en una cifra antes de que la decisión deje de ser la buena. Sobre el
caso (acciones → resultados con probabilidad → utilidad) se calcula:

- **Sensibilidad a las probabilidades**: para la acción recomendada y su rival más
  cercano, a partir de qué probabilidad de cada resultado cambiaría la mejor
  acción (el resto de la probabilidad se reparte en proporción).
- **Sensibilidad a las utilidades**: cuánto tendría que valer cada resultado para
  que gane otra acción.
- **Valor de la información (EVPI por acción)**: cuánta utilidad esperada se gana
  si, ANTES de decidir, supieras cómo saldría esa acción:
  E_o[max(u(o), mejor alternativa)] − max(EU(acción), mejor alternativa).
  Si es alto, merece la pena averiguarlo (preguntar, probar en pequeño) antes de
  comprometerse.

Todo es aritmética sobre el mismo planificador (utilidad ajustada a riesgo y
descarte por ruina), a un paso de decisión: no cuesta llamadas a la IA.
"""

from __future__ import annotations

from dataclasses import replace

from oraqlo.casefile import CaseSpec
from oraqlo.simulator.base import Action, Trajectory
from oraqlo.strategy.planner import ExpectedUtilityPlanner, RiskProfile
from oraqlo.world_model import WorldModel

STEPS = 40  # resolución del barrido antes de afinar con bisección
BISECT = 20
UTILITY_RANGE = 2.0  # cuánto se explora por encima/debajo de cada utilidad


def effective_outcomes(spec: CaseSpec) -> dict[str, dict[str, float]]:
    """Distribución de resultados de cada acción tal como la ve el simulador (con el
    rival, si el caso lo tiene)."""
    state = WorldModel().current()
    model = spec.outcome_model()
    return {a.id: dict(model(state, a).outcomes) for a in spec.actions}


def _ranking(
    actions: list[Action],
    outcomes: dict[str, dict[str, float]],
    utilities: dict[str, float],
    risk: RiskProfile,
) -> list[tuple[str, float, float]]:
    """[(id, EU, EU ajustada)] de mejor a peor, sin las acciones descartadas por ruina."""
    trajectories = [
        Trajectory(states=[], probability=p, utility=utilities[o], actions_taken=[a])
        for a in actions
        for o, p in outcomes[a.id].items()
    ]
    recs = ExpectedUtilityPlanner().decide(trajectories, risk)
    return [(r.action.id, r.expected_utility, r.risk_adjusted_utility) for r in recs]


def _best(actions, outcomes, utilities, risk) -> str | None:
    ranking = _ranking(actions, outcomes, utilities, risk)
    return ranking[0][0] if ranking else None


def _shift(dist: dict[str, float], label: str, p: float) -> dict[str, float]:
    """`dist` con P(label) = p y el resto repartido en proporción a como estaba."""
    rest = 1.0 - dist[label]
    out = {}
    for k, v in dist.items():
        if k == label:
            out[k] = p
        elif rest > 1e-12:
            out[k] = v / rest * (1.0 - p)
        else:
            out[k] = (1.0 - p) / (len(dist) - 1)
    return out


def _first_flip(values: list[float], best_at, current_best: str) -> tuple[float, str] | None:
    """Primer valor del barrido (en orden) donde la mejor acción deja de ser la actual,
    afinado por bisección con el anterior. None si no cambia en todo el barrido."""
    prev = values[0]
    for value in values[1:]:
        winner = best_at(value)
        if winner != current_best:
            lo, hi = prev, value
            for _ in range(BISECT):
                mid = (lo + hi) / 2
                if best_at(mid) == current_best:
                    lo = mid
                else:
                    hi = mid
            return hi, best_at(hi) or ""
        prev = value
    return None


def analyze(spec: CaseSpec) -> dict:
    """Puntos de ruptura y valor de la información del caso (ver docstring del módulo)."""
    actions = spec.actions
    outcomes = effective_outcomes(spec)
    utilities = dict(spec.utilities)
    risk = spec.risk
    names = {a.id: a.description for a in actions}
    ranking = _ranking(actions, outcomes, utilities, risk)
    if not ranking:
        return {"best": None, "ranking": [], "probability": [], "utility": [], "voi": []}
    best = ranking[0][0]
    alive = [a for a in actions if a.id in {r[0] for r in ranking}]

    # Probabilidades: la recomendada y su rival más cercano.
    probability = []
    for action_id in [r[0] for r in ranking[:2]]:
        for label, p in outcomes[action_id].items():
            if len(outcomes[action_id]) < 2:
                continue

            def best_at(x: float, aid=action_id, lab=label) -> str | None:
                trial = dict(outcomes)
                trial[aid] = _shift(outcomes[aid], lab, x)
                return _best(actions, trial, utilities, risk)

            for direction, end in (("baja", 0.0), ("sube", 1.0)):
                sweep = [p + (end - p) * i / STEPS for i in range(STEPS + 1)]
                flip = _first_flip(sweep, best_at, best)
                if flip is not None:
                    probability.append(
                        {
                            "action": action_id,
                            "action_text": names[action_id],
                            "outcome": label,
                            "p": p,
                            "threshold": flip[0],
                            "direction": direction,
                            "new_best": flip[1],
                            "distance": abs(flip[0] - p),
                        }
                    )
    probability.sort(key=lambda item: item["distance"])

    # Utilidades de cada resultado.
    utility = []
    for label, u in utilities.items():
        if not any(label in outcomes[a.id] for a in actions):
            continue

        def best_u(x: float, lab=label) -> str | None:
            return _best(actions, outcomes, {**utilities, lab: x}, risk)

        for direction, end in (("baja", u - UTILITY_RANGE), ("sube", u + UTILITY_RANGE)):
            sweep = [u + (end - u) * i / STEPS for i in range(STEPS + 1)]
            flip = _first_flip(sweep, best_u, best)
            if flip is not None:
                utility.append(
                    {
                        "outcome": label,
                        "value": u,
                        "threshold": flip[0],
                        "direction": direction,
                        "new_best": flip[1],
                        "distance": abs(flip[0] - u),
                    }
                )
    utility.sort(key=lambda item: item["distance"])

    # Valor de saber de antemano cómo saldría cada acción (neutral al riesgo).
    eu = {a.id: sum(p * utilities[o] for o, p in outcomes[a.id].items()) for a in alive}
    voi = []
    for a in alive:
        others = [eu[b.id] for b in alive if b.id != a.id]
        if not others:
            continue
        alt = max(others)
        informed = sum(p * max(utilities[o], alt) for o, p in outcomes[a.id].items())
        value = informed - max(eu[a.id], alt)
        if value > 1e-9:
            voi.append({"action": a.id, "action_text": names[a.id], "value": value})
    voi.sort(key=lambda item: -item["value"])

    return {
        "best": best,
        "best_text": names[best],
        "ranking": [
            {"action": aid, "action_text": names[aid], "eu": e, "adjusted": adj}
            for aid, e, adj in ranking
        ],
        "probability": probability[:8],
        "utility": utility[:6],
        "voi": voi,
        "summary": summarize(names, probability, voi),
        "approximate": spec.depth > 1,
    }


def short(text: str, limit: int = 70) -> str:
    """Una acción larga, recortada para que la frase se lea de un vistazo."""
    text = " ".join(text.split()).rstrip(".")
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def summarize(names: dict[str, str], probability: list[dict], voi: list[dict]) -> list[str]:
    """Frases llanas: los puntos de ruptura más cercanos y lo que conviene averiguar."""
    lines = []
    for item in probability[:3]:
        verb = "bajara de" if item["direction"] == "baja" else "subiera de"
        other = short(names.get(item["new_best"], item["new_best"]))
        if item["new_best"] == item["action"]:
            then = "esa pasaría a ser la mejor opción"
        else:
            then = f"convendría más «{other}»"
        lines.append(
            f"Si la probabilidad de «{item['outcome']}» con «{short(item['action_text'])}» "
            f"{verb} {item['threshold']:.0%} (ahora {item['p']:.0%}), {then}."
        )
    if voi:
        top = voi[0]
        lines.append(
            f"Lo que más vale averiguar antes de decidir: cómo saldría «{short(top['action_text'])}» "
            f"(saberlo de antemano vale {top['value']:.2f} puntos de utilidad)."
        )
    return lines


def with_outcomes(spec: CaseSpec, outcomes: dict[str, dict[str, float]]) -> CaseSpec:
    """Copia del caso con otras probabilidades (útil para pruebas y para el editor)."""
    return replace(spec, outcomes=outcomes, rival=None)
