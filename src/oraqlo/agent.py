"""Agente Oraqlo: orquesta el bucle OODA sobre los módulos enchufables.

Observe: conectores → world model.
Orient:  forecaster + simulador sobre el estado actual.
Decide:  planificador → panel de decisión (debate adversarial).
Act:     emitir la recomendación y REGISTRAR cada predicción en el ledger de
         calibración — sin ese registro, el bucle de aprendizaje no existe.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from oraqlo.connectors.base import Connector
from oraqlo.forecaster.base import Forecast, Forecaster
from oraqlo.memory.calibration import CalibrationLedger
from oraqlo.panel import DecisionPanel, Verdict
from oraqlo.simulator.base import Action, ScenarioEngine
from oraqlo.strategy.planner import Planner, Recommendation, RiskProfile
from oraqlo.world_model import WorldModel

# Callback de progreso: (tipo, datos). Tipos: "phase" {name}, "forecast" {id, outcomes},
# "turn" {role, text}, "verdict" {decision, confidence, candidate}, "revision" {objections}.
OnEvent = Callable[[str, dict[str, Any]], None]


def _noop(kind: str, data: dict[str, Any]) -> None:
    pass


@dataclass
class StrategyReport:
    """Salida de un ciclo completo: predicción, recomendación y veredicto, auditables."""

    recommendation: Recommendation
    verdict: Verdict
    forecast: Forecast
    revisions: int = 0
    candidates_tried: int = 1


class OraqloAgent:
    """Composición explícita: cada dependencia se inyecta, nada se instancia dentro.

    Esto mantiene el motor agnóstico al dominio — el dominio entra por los
    conectores, las acciones candidatas y la función de utilidad del planner.
    """

    def __init__(
        self,
        connectors: list[Connector],
        world_model: WorldModel,
        forecaster: Forecaster,
        simulator: ScenarioEngine,
        planner: Planner,
        panel: DecisionPanel,
        ledger: CalibrationLedger,
        risk: RiskProfile | None = None,
    ) -> None:
        self.connectors = connectors
        self.world_model = world_model
        self.forecaster = forecaster
        self.simulator = simulator
        self.planner = planner
        self.panel = panel
        self.ledger = ledger
        self.risk = risk or RiskProfile()

    def observe(self) -> None:
        """Fase Observe: drena todos los conectores hacia el world model."""
        for connector in self.connectors:
            for obs in connector.poll():
                self.world_model.apply(obs)

    def cycle(
        self,
        question: str,
        candidate_actions: list[Action],
        horizon: timedelta,
        depth: int = 3,
        max_revisions: int = 2,
        on_event: OnEvent | None = None,
        extra_context: str = "",
    ) -> StrategyReport:
        """Un ciclo OODA completo.

        Observe → forecast (SIEMPRE registrado en el ledger antes de decidir) →
        simular → planificar → deliberar. Si el panel dice "revisar", se
        redelibera la misma candidata con las objeciones añadidas al contexto
        (máx. `max_revisions` veces); si dice "descartar", se pasa a la
        siguiente del ranking. Si se agotan candidatas, se devuelve el último
        veredicto tal cual (un informe con decision != "emitir" es una salida
        honesta: significa que ninguna opción sobrevivió al debate).
        """
        if not candidate_actions:
            raise ValueError("Se necesita al menos una acción candidata")

        emit = on_event or _noop

        emit("phase", {"name": "observar"})
        self.observe()
        state = self.world_model.current()

        emit("phase", {"name": "prever"})
        forecast = self.forecaster.forecast(state, question, horizon)
        self.ledger.record(forecast)
        emit("forecast", {"id": forecast.id, "outcomes": forecast.distribution.outcomes})

        emit("phase", {"name": "simular"})
        trajectories = self.simulator.expand(state, candidate_actions, depth)
        emit("phase", {"name": "planificar"})
        ranking = self.planner.decide(trajectories, self.risk)
        if not ranking:
            raise RuntimeError(
                "El planificador descartó todas las acciones (riesgo de ruina); "
                "no hay nada que deliberar. Revisa el RiskProfile o las candidatas."
            )

        context = self.build_context(question, horizon, forecast)
        if extra_context:
            context += "\n\n" + extra_context
        idx, revisions, total_revisions = 0, 0, 0
        while True:
            candidate = ranking[idx]
            emit("phase", {"name": "deliberar", "candidate": candidate.action.description})
            verdict = self.panel.deliberate(
                context,
                candidate,
                on_turn=lambda role, text: emit("turn", {"role": role, "text": text}),
            )
            emit(
                "verdict",
                {
                    "decision": verdict.decision,
                    "confidence": verdict.confidence,
                    "candidate": candidate.action.description,
                },
            )
            if verdict.decision == "emitir":
                break
            if verdict.decision == "revisar" and revisions < max_revisions:
                revisions += 1
                total_revisions += 1
                emit("revision", {"objections": verdict.objections})
                context += "\n\nObjeciones de la deliberación anterior (atiéndelas):\n" + "\n".join(
                    f"- {o}" for o in verdict.objections
                )
                continue
            if idx + 1 < len(ranking):
                idx += 1
                revisions = 0
                continue
            break  # sin más candidatas: se informa el veredicto negativo tal cual

        return StrategyReport(
            recommendation=candidate,
            verdict=verdict,
            forecast=forecast,
            revisions=total_revisions,
            candidates_tried=idx + 1,
        )

    @staticmethod
    def build_context(question: str, horizon: timedelta, forecast: Forecast) -> str:
        lines = [
            f"Pregunta estratégica: {question}",
            f"Horizonte: {horizon.days} días",
            "Previsión del oráculo:",
            *(
                f"  - {label}: {p:.0%}"
                for label, p in sorted(
                    forecast.distribution.outcomes.items(), key=lambda kv: -kv[1]
                )
            ),
        ]
        if forecast.assumptions:
            lines.append("Supuestos del oráculo:")
            lines.extend(f"  - {a}" for a in forecast.assumptions)
        return "\n".join(lines)

    _build_context = build_context  # compat (la TUI lo usa)

    def resolve(self, forecast_id: str, actual_outcome: str) -> None:
        """Cierra una predicción (y las de sus miembros si es un ensamble) con el
        resultado real: alimenta la calibración de cada fuente."""
        from oraqlo.forecaster.ensemble import resolve_group

        resolve_group(self.ledger, forecast_id, actual_outcome)
