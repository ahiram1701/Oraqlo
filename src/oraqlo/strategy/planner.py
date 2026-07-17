"""Planificador: elige la acción de mayor utilidad esperada ajustada a riesgo.

No optimiza solo la media: penaliza la cola (riesgo de ruina) según el perfil de
riesgo configurado, y respeta restricciones duras (una acción que las viola queda
descartada aunque su utilidad esperada sea máxima).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from oraqlo.simulator.base import Action, Trajectory


@dataclass
class RiskProfile:
    """Aversión al riesgo configurable.

    `risk_aversion` ∈ [0, 1]: 0 = neutral (solo media), 1 = maximin (solo peor caso).
    `cvar_alpha`: nivel de la cola a vigilar (p. ej. 0.05 = peor 5% de escenarios).
    `ruin_threshold`: utilidad por debajo de la cual un escenario cuenta como ruina;
    cualquier acción con probabilidad de ruina > `max_ruin_probability` se descarta.
    """

    risk_aversion: float = 0.3
    cvar_alpha: float = 0.05
    ruin_threshold: float | None = None
    max_ruin_probability: float = 0.01


@dataclass
class Recommendation:
    """Salida auditable del planificador: qué hacer, por qué, y con qué confianza."""

    action: Action
    expected_utility: float
    risk_adjusted_utility: float
    confidence: float
    assumptions: list[str] = field(default_factory=list)
    rejected_alternatives: list[tuple[Action, str]] = field(default_factory=list)
    review_triggers: list[str] = field(default_factory=list)


class Planner(ABC):
    """Contrato del planificador."""

    @abstractmethod
    def decide(
        self,
        trajectories: list[Trajectory],
        risk: RiskProfile,
        constraints: list[str] | None = None,
    ) -> list[Recommendation]:
        """Ranking de recomendaciones (mejor primero) sobre las trayectorias simuladas."""
        ...


def _cvar(pairs: list[tuple[float, float]], alpha: float) -> float:
    """CVaR de la cola inferior: utilidad media del peor `alpha` de masa de probabilidad.

    `pairs` son (peso, utilidad) con pesos normalizados a 1. Toma trayectorias
    de peor a mejor utilidad hasta acumular `alpha`, con inclusión parcial de
    la frontera.
    """
    remaining, acc = alpha, 0.0
    for weight, utility in sorted(pairs, key=lambda wu: wu[1]):
        take = min(weight, remaining)
        acc += take * utility
        remaining -= take
        if remaining <= 1e-12:
            break
    return acc / alpha


class ExpectedUtilityPlanner(Planner):
    """Utilidad esperada ajustada a riesgo sobre grupos de acción inicial.

    1. Agrupa trayectorias por `actions_taken[0]` y normaliza pesos por grupo.
    2. EU = Σ w·u;  EU_adj = (1−λ)·EU + λ·CVaR_α, con λ = risk_aversion.
    3. Descarta acciones con P(utilidad < ruin_threshold) > max_ruin_probability;
       si TODAS caen por ruina, devuelve lista vacía (negarse a recomendar es
       una salida válida — mejor que recomendar la ruina menos mala).
    4. `constraints` son restricciones textuales que este planner no puede
       evaluar: se adjuntan como supuestos para que el Panel las vigile.

    La confianza es una heurística de margen (distancia al mejor rival,
    normalizada y recortada a [0.05, 0.95]); la confianza final la fija el
    Juez del panel, no el planner.
    """

    def decide(
        self,
        trajectories: list[Trajectory],
        risk: RiskProfile,
        constraints: list[str] | None = None,
    ) -> list[Recommendation]:
        if not trajectories:
            return []

        groups: dict[str, tuple[Action, list[Trajectory]]] = {}
        for t in trajectories:
            if not t.actions_taken:
                raise ValueError("Trayectoria sin acción inicial: el planner no puede agruparla")
            action = t.actions_taken[0]
            groups.setdefault(action.id, (action, []))[1].append(t)

        rejected: list[tuple[Action, str]] = []
        scored: list[tuple[Action, float, float]] = []  # (acción, EU, EU ajustada)
        for action, trajs in groups.values():
            total_p = sum(t.probability for t in trajs)
            if total_p <= 0:
                rejected.append((action, "grupo sin masa de probabilidad"))
                continue
            pairs = [(t.probability / total_p, t.utility) for t in trajs]
            eu = sum(w * u for w, u in pairs)
            if risk.ruin_threshold is not None:
                ruin_p = sum(w for w, u in pairs if u < risk.ruin_threshold)
                if ruin_p > risk.max_ruin_probability:
                    rejected.append(
                        (action, f"P(ruina)={ruin_p:.1%} supera el máximo {risk.max_ruin_probability:.1%}")
                    )
                    continue
            adjusted = (1 - risk.risk_aversion) * eu + risk.risk_aversion * _cvar(pairs, risk.cvar_alpha)
            scored.append((action, eu, adjusted))

        scored.sort(key=lambda item: item[2], reverse=True)
        assumptions = [f"Restricción a vigilar: {c}" for c in (constraints or [])]

        recommendations: list[Recommendation] = []
        for i, (action, eu, adjusted) in enumerate(scored):
            if len(scored) == 1:
                confidence = 0.5
            else:
                best_other = max(adj for j, (_, _, adj) in enumerate(scored) if j != i)
                denom = max(abs(adjusted), abs(best_other), 1e-9)
                confidence = min(max(0.5 + 0.5 * (adjusted - best_other) / denom, 0.05), 0.95)
            recommendations.append(
                Recommendation(
                    action=action,
                    expected_utility=eu,
                    risk_adjusted_utility=adjusted,
                    confidence=confidence,
                    assumptions=list(assumptions),
                    rejected_alternatives=list(rejected),
                )
            )
        return recommendations
