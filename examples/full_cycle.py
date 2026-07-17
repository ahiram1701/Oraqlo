"""Ciclo OODA completo con LLMs reales: el agente Oraqlo de punta a punta.

Observe: un conector inyecta hechos del caso al world model.
Orient:  el LLMForecaster (Ollama) emite la previsión — registrada en el ledger.
         El simulador expectimax expande las acciones candidatas (dominio toy).
Decide:  el planificador ranquea por utilidad ajustada a riesgo y el panel
         de 4 roles LLM delibera sobre la mejor candidata.
Act:     se emite el StrategyReport auditable.

Uso:
    python examples/full_cycle.py --backend cloud [--model gpt-oss:120b-cloud]
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oraqlo.agent import OraqloAgent
from oraqlo.connectors import StaticConnector
from oraqlo.forecaster import Distribution, LLMForecaster
from oraqlo.llm import OllamaBackendConfig, OllamaProvider, OllamaUnavailableError
from oraqlo.memory import CalibrationLedger
from oraqlo.panel import ROLE_PROMPTS, PanelError, RoleConfig, SequentialPanel
from oraqlo.simulator import Action, TreeSearchEngine
from oraqlo.strategy import ExpectedUtilityPlanner, RiskProfile
from oraqlo.world_model import Observation, WorldModel

QUESTION = (
    "Una cadena regional de 12 cafeterías considera cómo responder a la llegada de "
    "una franquicia internacional a su ciudad. ¿Qué situación tendrá en 9 meses?"
)

ACTIONS = [
    Action(id="diferenciarse", description="Reposicionarse en café de especialidad local, subir precios 10% y reforzar la comunidad"),
    Action(id="competir-precio", description="Bajar precios 15% y lanzar programa de puntos agresivo"),
    Action(id="no-hacer-nada", description="Mantener la operación actual y observar 2 trimestres"),
]

# Dominio toy: probabilidades y utilidades de negocio codificadas a mano.
# En producción, el OutcomeModel vendría del propio Forecaster.
OUTCOMES = {
    "diferenciarse": {"fideliza y crece": 0.45, "nicho estable": 0.35, "pierde clientes sensibles a precio": 0.20},
    "competir-precio": {"defiende cuota": 0.30, "guerra de precios erosiva": 0.50, "quiebra de márgenes": 0.20},
    "no-hacer-nada": {"erosión lenta": 0.60, "cliente fiel resiste": 0.30, "colapso de tráfico": 0.10},
}
UTILITIES = {
    "fideliza y crece": 1.0, "nicho estable": 0.5, "pierde clientes sensibles a precio": -0.2,
    "defiende cuota": 0.4, "guerra de precios erosiva": -0.5, "quiebra de márgenes": -1.0,
    "erosión lenta": -0.3, "cliente fiel resiste": 0.2, "colapso de tráfico": -1.0,
}

ROLE_TEMPERATURES = {"estratega": 0.7, "red_team": 0.9, "oraculo": 0.2, "juez": 0.1}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["local", "cloud"], default="cloud")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    if args.backend == "cloud":
        config, model = OllamaBackendConfig.cloud(), args.model or "gpt-oss:120b-cloud"
    else:
        config, model = OllamaBackendConfig.local(), args.model or "llama3.1"

    provider = OllamaProvider(config)
    if not provider.is_available():
        print(f"Backend {args.backend} ({config.host}) no disponible.")
        return 1

    agent = OraqloAgent(
        connectors=[StaticConnector("caso", [
            Observation(entity_id="cadena", variable="locales", value=12, source="caso"),
            Observation(entity_id="cadena", variable="caja_meses", value=14, source="caso"),
            Observation(entity_id="competidor", variable="aperturas_previstas", value=3, source="caso"),
        ])],
        world_model=WorldModel(),
        forecaster=LLMForecaster(provider, model=model),
        simulator=TreeSearchEngine(
            outcome_model=lambda state, action: Distribution(outcomes=OUTCOMES[action.id]),
            utility_fn=lambda state, path: UTILITIES[path[-1]],
        ),
        planner=ExpectedUtilityPlanner(),
        panel=SequentialPanel(roles={
            name: RoleConfig(name=name, provider=provider, model=model,
                             temperature=ROLE_TEMPERATURES[name])
            for name in ROLE_PROMPTS
        }),
        ledger=CalibrationLedger(),
        risk=RiskProfile(risk_aversion=0.3, ruin_threshold=-0.9, max_ruin_probability=0.25),
    )

    print(f"Ciclo OODA completo · {model} @ {args.backend}\n")
    print(f"Pregunta: {QUESTION}\n")
    try:
        report = agent.cycle(QUESTION, ACTIONS, horizon=timedelta(days=270), depth=1)
    except (OllamaUnavailableError, PanelError) as e:
        print(f"El ciclo falló: {e}")
        return 1

    print("── Previsión del oráculo (registrada en el ledger) ──")
    for label, p in sorted(report.forecast.distribution.outcomes.items(), key=lambda kv: -kv[1]):
        print(f"  {p:>6.1%}  {label}")

    print("\n── Recomendación del planificador ──")
    rec = report.recommendation
    print(f"  Acción: {rec.action.description}")
    print(f"  EU: {rec.expected_utility:+.3f} · ajustada a riesgo: {rec.risk_adjusted_utility:+.3f}")
    for action, reason in rec.rejected_alternatives:
        print(f"  Descartada por el planner: {action.id} ({reason})")

    print("\n── Veredicto del panel ──")
    v = report.verdict
    print(f"  {v.decision.upper()}  (confianza: {v.confidence:.0%}"
          + (f", P(éxito) oráculo: {v.p_success:.0%}" if v.p_success is not None else "") + ")")
    print(f"  Síntesis: {v.rationale}")
    for t in v.review_triggers:
        print(f"  Disparador de revisión: {t}")
    print(f"\n  Candidatas debatidas: {report.candidates_tried} · revisiones: {report.revisions}")
    print(f"  Predicciones abiertas en el ledger: {len(agent.ledger.open_forecasts)} "
          f"(id: {report.forecast.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
