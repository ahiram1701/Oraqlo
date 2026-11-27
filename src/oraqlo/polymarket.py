"""Cliente de solo lectura de Polymarket (Gamma API): banco público de preguntas
con resolución objetiva para la práctica autónoma de Oraqlo.

SOLO GET, sin credenciales, sin trading — Polymarket se usa exclusivamente como
fuente de preguntas y de verdades-resueltas. Esquema verificado contra la API:
`outcomes` y `outcomePrices` llegan como strings JSON; un mercado resuelto tiene
`umaResolutionStatus == "resolved"` y el ganador es el outcome con precio ≈ 1.
"""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from oraqlo.net import get_json

GAMMA_HOST = "https://gamma-api.polymarket.com"


class PolymarketError(RuntimeError):
    """La API de Polymarket no respondió o devolvió algo inesperado."""

    def __init__(self, message: str, offline: bool = False) -> None:
        super().__init__(message)
        # Sin conexión (DNS, red caída, PC recién despertado): no es un fallo de Oraqlo.
        self.offline = offline


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

    @property
    def voided(self) -> bool:
        """Resuelto sin ganador (p. ej. 0.5/0.5: partido anulado o retirada). Nunca
        tendrá ganador: las previsiones sobre él se descartan."""
        return self.resolved and self.winner is None

    @property
    def uma_status(self) -> str | None:
        """Fase de la resolución en UMA: None (sin proponer), proposed, disputed, resolved."""
        value = (self.raw or {}).get("umaResolutionStatus")
        return str(value).lower() if value else None

    @property
    def rules(self) -> str:
        """Las reglas de resolución (qué cuenta como cada resultado), tal cual las publica."""
        return str((self.raw or {}).get("description") or "")


DECIDED_PRICE = 0.97


def already_decided(market: Market, now: datetime) -> bool:
    """Polymarket deja abiertos hasta su fecha de cierre partidos ya jugados: el precio
    ya marca el resultado y predecirlos no enseña nada (ni el duelo con el mercado es
    justo). Se descartan los de precio casi seguro, los que ya no aceptan órdenes y
    los partidos que ya empezaron."""
    if market.outcomes and max(market.outcomes.values()) >= DECIDED_PRICE:
        return True
    raw = market.raw or {}
    if raw.get("acceptingOrders") is False:
        return True
    start = raw.get("gameStartTime") or raw.get("eventStartTime")
    if start:
        with contextlib.suppress(ValueError, TypeError):
            when = datetime.fromisoformat(str(start).replace("Z", "+00:00").replace(" ", "T"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            if when <= now:
                return True
    return False


def _parse_market(data: dict) -> Market:
    try:
        labels = json.loads(data.get("outcomes") or "[]")
        prices = [float(p) for p in json.loads(data.get("outcomePrices") or "[]")]
        volume_24h = float(data.get("volume24hr") or 0.0)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        raise PolymarketError(f"Mercado {data.get('id')}: outcomes/prices ilegibles: {e}") from e
    if len(labels) != len(prices):  # emparejarlos a medias inventaría probabilidades
        raise PolymarketError(
            f"Mercado {data.get('id')}: {len(labels)} resultados y {len(prices)} precios."
        )
    outcomes = {str(label): price for label, price in zip(labels, prices, strict=True)}

    end_date = None
    if data.get("endDate"):
        with contextlib.suppress(ValueError):
            end_date = datetime.fromisoformat(str(data["endDate"]).replace("Z", "+00:00"))

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
        volume_24h=volume_24h,
        raw=data,
    )


class PolymarketClient:
    def __init__(self, host: str = GAMMA_HOST, timeout_s: float = 30.0) -> None:
        self.host = host.rstrip("/")
        self.timeout_s = timeout_s

    def _get(
        self, path: str, params: Mapping[str, object] | Sequence[tuple[str, object]] | None = None
    ):
        url = f"{self.host}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            return get_json(url, {"User-Agent": "oraqlo-practice"}, timeout_s=self.timeout_s)
        except urllib.error.HTTPError as e:
            raise PolymarketError(f"HTTP {e.code} de Polymarket en {path}") from e
        except json.JSONDecodeError as e:
            raise PolymarketError(f"Respuesta ilegible de Polymarket ({path}): {e}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise PolymarketError(
                f"No se pudo consultar Polymarket ({path}): {e}", offline=True
            ) from e

    def list_open(
        self,
        limit: int = 20,
        min_volume_24h: float = 10_000.0,
        max_days_to_close: int = 30,
        min_days_to_close: int = 1,
        max_pages: int = 8,
        exclude: set[str] | frozenset[str] = frozenset(),
    ) -> list[Market]:
        """Mercados abiertos ordenados por volumen, que cierran dentro de la ventana.

        Ventana corta = feedback rápido para el bucle de aprendizaje; volumen
        mínimo = preguntas serias con precio informativo. Los ids de `exclude` (los ya
        predichos) no cuentan para `limit`: así no hace falta pedir más páginas de la cuenta.
        """
        now = datetime.now(UTC)
        markets: list[Market] = []
        page = 100  # el máximo que devuelve Gamma por página
        # Por páginas: la mayoría no pasa los filtros (casi decididos, fuera de plazo).
        for offset in range(0, page * max_pages, page):
            data = self._get(
                "/markets",
                {
                    "closed": "false",
                    "order": "volume24hr",
                    "ascending": "false",
                    "limit": page,
                    "offset": offset,
                },
            )
            if not isinstance(data, list):
                raise PolymarketError("Respuesta inesperada al listar mercados (no es una lista).")
            for item in data:
                try:
                    market = _parse_market(item)
                except PolymarketError:
                    continue
                if market.id in exclude:
                    continue
                if not market.outcomes or market.closed or market.end_date is None:
                    continue
                days = (market.end_date - now).total_seconds() / 86400
                if not min_days_to_close <= days <= max_days_to_close:
                    continue
                if market.volume_24h < min_volume_24h:
                    continue
                if already_decided(market, now):
                    continue
                markets.append(market)
                if len(markets) >= limit:
                    return markets
            if len(data) < page:
                break
        return markets

    def search(self, query: str, limit: int = 10) -> list[Market]:
        """Mercados abiertos que coinciden con `query` (búsqueda pública de Gamma).

        Para usar el precio del mercado como una voz más al predecir de verdad (no en
        la Práctica, que es a ciegas)."""
        data = self._get(
            "/public-search",
            {"q": query, "limit_per_type": limit, "events_status": "active"},
        )
        events = data.get("events") if isinstance(data, dict) else None
        found: list[Market] = []
        for event in events or []:
            for item in (event or {}).get("markets") or []:
                try:
                    market = _parse_market(item)
                except PolymarketError:
                    continue
                if market.outcomes and not market.closed and not market.resolved:
                    if not market.raw.get("description") and event.get("description"):
                        market.raw["description"] = event["description"]
                    found.append(market)
                if len(found) >= limit:
                    return found
        return found

    BATCH = 50  # ids por petición en get_markets

    def get_markets(self, market_ids: list[str], closed: bool) -> dict[str, Market]:
        """Varios mercados en pocas peticiones (`?id=..&id=..`). Gamma filtra por
        `closed`: con False no devuelve los cerrados y con True solo los cerrados.
        Los ids que no aparecen simplemente no están en el resultado."""
        found: dict[str, Market] = {}
        ids = list(dict.fromkeys(market_ids))
        for i in range(0, len(ids), self.BATCH):
            chunk = ids[i : i + self.BATCH]
            params = [("id", mid) for mid in chunk]
            params += [("closed", "true" if closed else "false"), ("limit", len(chunk))]
            data = self._get("/markets", params)
            if not isinstance(data, list):
                raise PolymarketError("Respuesta inesperada al pedir mercados (no es una lista).")
            for item in data:
                try:
                    market = _parse_market(item)
                except PolymarketError:
                    continue
                found[market.id] = market
        return found

    def get_market(self, market_id: str) -> Market:
        data = self._get(f"/markets/{market_id}")
        if isinstance(data, list):  # algunos despliegues devuelven lista de 1
            if not data:
                raise PolymarketError(f"Mercado {market_id} no encontrado.")
            data = data[0]
        return _parse_market(data)
