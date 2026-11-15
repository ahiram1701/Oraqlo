"""Compatibilidad: `python tui.py [caso.json]` equivale a `oraqlo tui [caso.json]`.

Requiere el paquete instalado (`uv sync` o `pip install -e .[tui]`).
"""

import sys

from oraqlo.cli import main

raise SystemExit(main(["tui", *sys.argv[1:]]))
