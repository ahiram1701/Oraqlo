"""Bucle de aprendizaje completo con un LLM real: predecir → registrar → resolver → puntuar.

1. LLMForecaster pide al modelo una distribución de escenarios (JSON validado).
2. CalibrationLedger registra la predicción abierta.
3. Simulamos que llega el resultado real y la resolvemos.
4. El Brier resultante alimenta reliability(fuente) — el peso del ensamble.

Uso:
    python examples/learning_loop.py --backend cloud [--model gpt-oss:120b-cloud]
    python examples/learning_loop.py --backend local [--model llama3.1]
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oraqlo.forecaster import ForecastParseError, LLMForecaster
from oraqlo.llm import OllamaBackendConfig, OllamaProvider, OllamaUnavailableError
from oraqlo.memory import CalibrationLedger
from oraqlo.world_model import WorldModel

QUESTION = (
    "Una startup de 10 personas lanza su producto en un mercado con 2 competidores "
    "consolidados. ¿Cuál será su situación en 12 meses?"
)


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

    forecaster = LLMForecaster(provider, model=model)
    ledger = CalibrationLedger()

    print(f"── 1. Predicción ({forecaster.source} @ {args.backend}) ──")
    print(f"Pregunta: {QUESTION}\n")
    try:
        forecast = forecaster.forecast(WorldModel().current(), QUESTION, timedelta(days=365))
    except (OllamaUnavailableError, ForecastParseError) as e:
        print(f"Fallo: {e}")
        return 1

    for label, p in sorted(forecast.distribution.outcomes.items(), key=lambda kv: -kv[1]):
        print(f"  {p:>6.1%}  {label}")
    print("\nSupuestos declarados:")
    for a in forecast.assumptions:
        print(f"  - {a}")

    print("\n── 2. Registro en el ledger ──")
    ledger.record(forecast)
    print(f"Predicción abierta: {forecast.id}  (abiertas: {len(ledger.open_forecasts)})")

    # En producción esto ocurre semanas/meses después, cuando el mundo responde.
    # Aquí lo simulamos tomando el escenario más probable como "lo que pasó".
    actual = max(forecast.distribution.outcomes, key=forecast.distribution.outcomes.get)
    print("\n── 3. Llega el resultado real (simulado) ──")
    print(f"Resultado observado: {actual!r}")
    resolved = ledger.resolve(forecast.id, actual)

    print("\n── 4. Puntuación y calibración ──")
    print(f"Brier multiclase: {resolved.brier:.4f}  (0 = perfecto, 2 = certeza equivocada)")
    print(f"Fiabilidad histórica de {forecast.source}: {ledger.reliability(forecast.source):.4f}")
    print("\nEste número es el que el EnsembleForecaster usará para ponderar esta fuente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
