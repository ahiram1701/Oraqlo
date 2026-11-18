"""Configuración común de los tests."""

import os

# Nunca mostrar avisos reales de Windows desde los tests.
os.environ["ORAQLO_DESKTOP_NOTIFY"] = "0"

# El servidor solo acepta Host 127.0.0.1/localhost o *.ts.net (defensa contra DNS
# rebinding). TestClient usa "testserver" por defecto: que hable como el navegador
# del propio PC.
import fastapi.testclient as _tc

_original_init = _tc.TestClient.__init__


def _local_init(self, app, base_url="http://127.0.0.1", *args, **kwargs):
    _original_init(self, app, base_url, *args, **kwargs)


_tc.TestClient.__init__ = _local_init  # type: ignore[method-assign]


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _sin_catalogo_de_la_nube(monkeypatch):
    """Los tests no consultan ollama.com: sin catálogo, sin comité automático."""
    monkeypatch.setattr("oraqlo.services.core.fetch_cloud_catalog", lambda *a, **k: [])


@pytest.fixture(autouse=True)
def _sin_voces_extra(request, monkeypatch):
    """Ni mercados de verdad (red) ni estimación por partes (llamadas de más al modelo
    falso) salvo en los tests marcados con `@pytest.mark.voces`, que traen las suyas."""
    if request.node.get_closest_marker("voces"):
        return
    monkeypatch.setattr("oraqlo.forecaster.markets.default_sources", lambda: [])
    monkeypatch.setattr("oraqlo.forecaster.decompose.decompose", lambda *a, **k: None)


def pytest_configure(config):
    config.addinivalue_line("markers", "voces: el test usa mercados o estimación por partes")
