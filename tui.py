"""Lanzador de la TUI sin instalar el paquete: python tui.py [ruta/al/caso.json]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

try:
    from oraqlo.tui.app import main
except ModuleNotFoundError as e:
    if "textual" in str(e):
        print("La TUI necesita Textual: pip install textual")
        raise SystemExit(1) from e
    raise

main()
