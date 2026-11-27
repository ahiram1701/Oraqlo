"""Defensas de acceso (api/auth.py), probadas directamente sobre peticiones sueltas."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from oraqlo.api.auth import AuthError, check_request, identify


def _request(
    method: str = "GET", headers: dict[str, str] | None = None, client: str = "127.0.0.1"
) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/x",
            "headers": raw,
            "client": (client, 5000),
            "query_string": b"",
        }
    )


@pytest.mark.parametrize(
    "host", ["127.0.0.1:8765", "localhost", "[::1]:8765", "mi-pc.tail1234.ts.net"]
)
def test_hosts_permitidos(host):
    check_request(_request(headers={"host": host}))


@pytest.mark.parametrize("host", ["", "evil.com", "testclient", "127.0.0.1.evil.com"])
def test_hosts_rechazados(host):
    with pytest.raises(AuthError) as e:
        check_request(_request(headers={"host": host}))
    assert e.value.status == 400


def test_post_de_otro_sitio_se_bloquea():
    host = {"host": "127.0.0.1:8765"}
    for extra in (
        {"sec-fetch-site": "cross-site"},
        {"origin": "http://evil.com"},
        {"origin": "http://127.0.0.1:9999"},  # otro puerto = otra app
        {"origin": "null"},
    ):
        with pytest.raises(AuthError) as e:
            check_request(_request("POST", {**host, **extra}))
        assert e.value.status == 403
    check_request(_request("POST", {**host, "origin": "http://127.0.0.1:8765"}))
    check_request(_request("POST", host))  # sin Origin (curl, la propia app)


def test_identidad_local_y_de_tailscale():
    assert identify(_request(), []).via == "local"
    who = identify(
        _request(headers={"tailscale-user-login": "ana@x", "tailscale-user-name": "Ana"}), []
    )
    assert (who.via, who.login, who.name) == ("tailscale", "ana@x", "Ana")


def test_proxy_sin_identidad_funnel_y_usuarios_no_autorizados():
    with pytest.raises(AuthError) as e:
        identify(_request(headers={"x-forwarded-for": "1.2.3.4"}), [])
    assert e.value.status == 401
    with pytest.raises(AuthError) as e:
        identify(_request(headers={"tailscale-funnel-request": "?1"}), [])
    assert e.value.status == 403
    with pytest.raises(AuthError) as e:
        identify(_request(headers={"tailscale-user-login": "eve@x"}), ["ana@x"])
    assert e.value.status == 403
    assert identify(_request(headers={"tailscale-user-login": "ana@x"}), ["ana@x"]).login


def test_cliente_remoto_sin_tailscale_se_rechaza():
    with pytest.raises(AuthError):
        identify(_request(client="192.168.1.20"), [])
    with pytest.raises(AuthError):  # el nombre que usa TestClient ya no es «local»
        identify(_request(client="testclient"), [])
