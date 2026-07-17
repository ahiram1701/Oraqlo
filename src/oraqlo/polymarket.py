"""Cliente de solo lectura de Polymarket (Gamma API): banco público de preguntas
con resolución objetiva para la práctica autónoma de Oraqlo.

SOLO GET, sin credenciales, sin trading — Polymarket se usa exclusivamente como
fuente de preguntas y de verdades-resueltas. Esquema verificado contra la API:
`outcomes` y `outcomePrices` llegan como strings JSON; un mercado resuelto tiene
`umaResolutionStatus == "resolved"` y el ganador es el outcome con precio ≈ 1.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

GAMMA_HOST = "https://gamma-api.polymarket.com"


class PolymarketError(RuntimeError):
    """La API de Polymarket no respondió o devolvió algo inesperado."""


@dataclass
class Market:
    id: str
    question: str
    outcomes: dict[str, float]  # outcome → probabilidad actual del mercado
    end_date: datetime | None
    closed: bool
    resolved: bool
    winner: str | None = None
    volume_24h: float = 0.0
    raw: dict = field(default_factory=dict)


def _parse_market(data: dict) -> Market:
    try:
        labels = json.loads(data.get("outcomes") or "[]")
        prices = [float(p) for p in json.loads(data.get("outcomePrices") or "[]")]
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        raise PolymarketError(f"Mercado {data.get('id')}: outcomes/prices ilegibles: {e}") from e
    outcomes = {str(label): price for label, price in zip(labels, prices)}

    end_date = None
    if data.get("endDate"):
        try:
            end_date = datetime.fromisoformat(str(data["endDate"]).replace("Z", "+00:00"))
        except ValueError:
            pass

    resolved = str(data.get("umaResolutionStatus") or "").lower() == "resolved"
    winner = None
    if resolved and outcomes:
        top_label, top_price = max(outcomes.items(), key=lambda kv: kv[1])
        if top_price >= 0.99:
            winner = top_label

    return Market(
        id=str(data.get("id")),
        question=str(data.get("question", "")).strip(),
        outcomes=outcomes,
        end_date=end_date,
        closed=bool(data.get("closed")),
        resolved=resolved,
        winner=winner,
        volume_24h=float(data.get("volume24hr") or 0.0),
        raw=data,
    )


class PolymarketClient:
    def __init__(self, host: str = GAMMA_HOST, timeout_s: float = 30.0) -> None:
        self.host = host.rstrip("/")
        self.timeout_s = timeout_s

    def _get(self, path: str, params: dict | None = None):
        url = f"{self.host}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "oraqlo-practice"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise PolymarketError(f"HTTP {e.code} de Polymarket en {path}") from e
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            raise PolymarketError(f"No se pudo consultar Polymarket ({path}): {e}") from e

    def list_open(
        self,
        limit: int = 20,
        min_volume_24h: float = 10_000.0,
        max_days_to_close: int = 30,
        min_days_to_close: int = 1,
    ) -> list[Market]:
        """Mercados abiertos ordenados por volumen, que cierran dentro de la ventana.

        Ventana corta = feedback rápido para el bucle de aprendizaje; volumen
        mínimo = preguntas serias con precio informativo.
        """
        data = self._get(
            "/markets",
            {"closed": "false", "order": "volume24hr", "ascending": "false", "limit": limit * 4},
        )
        if not isinstance(data, list):
            raise PolymarketError("Respuesta inesperada al listar mercados (no es una lista).")
        now = datetime.now(timezone.utc)
        markets = []
        for item in data:
            try:
                market = _parse_market(item)
            except PolymarketError:
                continue
            if not market.outcomes or market.closed or market.end_date is None:
                continue
            days = (market.end_date - now).total_seconds() / 86400
            if not min_days_to_close <= days <= max_days_to_close:
                continue
            if market.volume_24h < min_volume_24h:
                continue
            markets.append(market)
            if len(markets) >= limit:
                break
        return markets

    def get_market(self, market_id: str) -> Market:
        data = self._get(f"/markets/{market_id}")
        if isinstance(data, list):  # algunos despliegues devuelven lista de 1
            if not data:
                raise PolymarketError(f"Mercado {market_id} no encontrado.")
            data = data[0]
        return _parse_market(data)
