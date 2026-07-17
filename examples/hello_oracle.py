"""Demostración real de la integración Ollama (local o cloud).

Pide al modelo 3 escenarios con probabilidades — el gesto básico del oráculo.

Uso:
    python examples/hello_oracle.py --backend local  [--model llama3.1]
    python examples/hello_oracle.py --backend cloud  [--model gpt-oss:120b-cloud]
        (cloud requiere la variable de entorno OLLAMA_API_KEY)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# La consola de Windows usa cp1252 por defecto y no puede imprimir toda la
# salida de los modelos; forzamos UTF-8 sin romper otros entornos.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oraqlo.llm import ChatMessage, OllamaBackendConfig, OllamaProvider, OllamaUnavailableError

PROMPT = (
    "Un equipo de software debe decidir si migra su monolito a microservicios este "
    "trimestre. Genera exactamente 3 escenarios de resultado a 6 meses, cada uno con "
    "una probabilidad (deben sumar 1.0) y los 2 supuestos clave de los que depende. "
    "Sé conciso."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["local", "cloud"], default="local")
    parser.add_argument("--model", default=None, help="Modelo Ollama a usar")
    args = parser.parse_args()

    if args.backend == "cloud":
        config = OllamaBackendConfig.cloud()
        model = args.model or "gpt-oss:120b-cloud"
    else:
        config = OllamaBackendConfig.local()
        model = args.model or "llama3.1"

    provider = OllamaProvider(config)

    print(f"Backend: {args.backend} ({config.host}) · Modelo: {model}\n")

    if not provider.is_available():
        if args.backend == "local":
            print("Ollama local no responde en", config.host)
            print("Arráncalo con `ollama serve` (o abre la app de Ollama) y reintenta.")
        else:
            print("El backend cloud no está accesible.")
            print("Comprueba que OLLAMA_API_KEY está definida y que tienes red.")
        return 1

    try:
        response = provider.chat(
            messages=[
                ChatMessage(role="system", content="Eres el Oráculo de Oraqlo: siempre probabilidades, nunca certezas."),
                ChatMessage(role="user", content=PROMPT),
            ],
            model=model,
            temperature=0.4,
        )
    except OllamaUnavailableError as e:
        print(f"No se pudo completar la llamada: {e}")
        return 1

    print(response.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
