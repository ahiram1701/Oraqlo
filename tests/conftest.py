"""Configuración común de los tests."""

import os

# Nunca mostrar avisos reales de Windows desde los tests.
os.environ["ORAQLO_DESKTOP_NOTIFY"] = "0"
