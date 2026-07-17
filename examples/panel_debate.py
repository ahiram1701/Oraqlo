"""Debate real del panel de decisión: Estratega ▸ Red Team ▸ Oráculo ▸ Juez.

Somete una recomendación candidata al debate adversarial interno con LLMs reales
vía Ollama y muestra el veredicto con su transcript completo (trazabilidad).

Uso:
    python examples/panel_debate.py --backend cloud [--model gpt-oss:120b-cloud]
    python examples/panel_debate.py --backend local [--model llama3.1]

Nota: aquí todos los roles usan el mismo modelo por simplicidad; en producción
cada rol puede tener su propio modelo y backend (config/roles.example.yaml).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oraqlo.llm import OllamaBackendConfig, OllamaProvider, OllamaUnavailableError
from oraqlo.panel import ROLE_PROMPTS, PanelError, RoleConfig, SequentialPanel
from oraqlo.simulator.base import Action
from oraqlo.strategy.planner import Recommendation

CONTEXT = (
    "Una empresa de logística mediana (200 empleados) opera con software propio de "
    "hace 15 años. Sus dos competidores principales acaban de adoptar plataformas "
    "modernas con optimización de rutas por IA. La empresa tiene caja para ~18 meses "
    "de operación normal y su equipo técnico son 4 personas que mantienen el sistema legado."
)

CANDIDATE = Recommendation(
    action=Action(
        id="migrar-gradual",
        description=(
            "Migración gradual: contratar una plataforma SaaS de optimización de rutas "
            "para el 20% de la flota como piloto de 3 meses, manteniendo el sistema "
            "legado en paralelo, antes de decidir una migración completa."
        ),
    ),
    expected_utility=0.68,
    risk_adjusted_utility=0.61,
    confidence=0.5,
    assumptions=[
        "el SaaS se integra con los datos actuales sin reescribir el legado",
        "el piloto del 20% es representativo de la operación completa",
    ],
)

# Temperaturas por rol según config/roles.example.yaml: creatividad para proponer,
# agresividad para atacar, estabilidad para puntuar, conservadurismo para juzgar.
ROLE_TEMPERATURES = {"estratega": 0.7, "red_team": 0.9, "oraculo": 0.2, "juez": 0.1}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["local", "cloud"], default="cloud")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    if args.backend == "cloud":
        config = OllamaBackendConfig.cloud()
        model = args.model or "gpt-oss:120b-cloud"
    else:
        config = OllamaBackendConfig.local()
        model = args.model or "llama3.1"

    provider = OllamaProvider(config)
    if not provider.is_available():
        print(f"Backend {args.backend} ({config.host}) no disponible.")
        return 1

    panel = SequentialPanel(
        roles={
            name: RoleConfig(name=name, provider=provider, model=model,
                             temperature=ROLE_TEMPERATURES[name])
            for name in ROLE_PROMPTS
        }
    )

    print(f"Panel: 4 roles sobre {model} @ {args.backend}\n")
    print(f"Caso: {CONTEXT}\n")
    print(f"Candidata: {CANDIDATE.action.description}\n")
    print("═" * 70)

    try:
        verdict = panel.deliberate(CONTEXT, CANDIDATE)
    except (OllamaUnavailableError, PanelError) as e:
        print(f"El debate falló: {e}")
        return 1

    for rol, texto in verdict.transcript:
        print(f"\n── {rol.upper()} " + "─" * (60 - len(rol)))
        print(texto.strip()[:1500])

    print("\n" + "═" * 70)
    print(f"\nVEREDICTO: {verdict.decision.upper()}  (confianza del Juez: {verdict.confidence:.0%})")
    if verdict.p_success is not None:
        print(f"P(éxito) según el Oráculo: {verdict.p_success:.0%}")
    print(f"Síntesis: {verdict.rationale}")
    if verdict.review_triggers:
        print("Disparadores de revisión (si se observan, la decisión caduca):")
        for t in verdict.review_triggers:
            print(f"  - {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
