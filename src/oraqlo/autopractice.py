"""Práctica autónoma: Oraqlo se mejora solo prediciendo eventos reales.

Bucle: elige mercados de Polymarket → predice (con investigación opcional) →
registra su predicción Y la del mercado (benchmark) → cuando el mercado se
resuelve, cierra ambas con el ganador → el Brier comparado y el consejo de
calibración derivado del historial se inyectan en las siguientes predicciones.

Solo lectura de Polymarket. Cero trading.

CLI: python -m oraqlo.autopractice [--markets 2] [--no-research] [--loop --interval-min 60]
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from oraqlo.forecaster.aggregate import (
    NO_DATA_BASE_NOTE,
    NO_DATA_NOTE,
    clamp,
    parse_base_rate,
    pool_log_odds,
    temper_without_data,
)
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.forecaster.committee import (
    COMMITTEE_MODEL,
    Member,
    build_members,
    combine,
    member_source,
    run_members,
    weights_note,
)
from oraqlo.forecaster.ensemble import MEMBER_SEP
from oraqlo.llm.base import LLMProvider
from oraqlo.llm.jsonchat import JsonChatError, chat_json
from oraqlo.memory.calibration import CalibrationLedger, ResolvedForecast
from oraqlo.memory.recalibration import (
    DECIDED,
    MARKET_SOURCE,
    Recalibration,
    counts_for_score,
    decided_markets,
    for_model,
    group_key,
    is_member,
    market_id_of,
    raw_of,
)
from oraqlo.polymarket import Market, PolymarketClient, PolymarketError
from oraqlo.research import Researcher, ResearchError

_TAG_RE = re.compile(r"^\[polymarket:(\d+)\]")

_FORECAST_PROMPT = (
    "Eres el Oráculo de Oraqlo: probabilidades calibradas, nunca certezas. "
    "Asigna probabilidad a CADA UNO de los resultados dados, usando EXACTAMENTE "
    "esas etiquetas (ni una más, ni una menos); deben sumar 1.0. Empieza por la "
    "tasa base (en casos parecidos, con qué frecuencia pasa cada cosa; dala también en "
    "cifras), ajústala con lo específico y evita 0 y 1. Lee las reglas de resolución: "
    "deciden qué cuenta como cada resultado. Si la pregunta es «¿pasará X antes de la "
    "fecha?», aplica el ritmo al que suelen pasar estas cosas a los días que quedan: lo "
    "más frecuente es que nada cambie. Si no tienes datos concretos de ESTE caso "
    "(ranking, forma, cuotas, encuestas, cifras recientes), quédate en la tasa base o "
    "cerca del reparto igual: una ventaja que solo intuyes no pasa del 55-60%. "
    "Responde SOLO con JSON: "
    '{"base_rate": "<clase de referencia y frecuencia>", '
    '"base_rate_outcomes": {"<etiqueta>": <prob según solo la tasa base>, ...}, '
    '"outcomes": {"<etiqueta>": <prob>, ...}, "assumptions": ["<supuesto>", ...]}'
)
RULES_CHARS = 1500  # de las reglas del mercado, lo que entra en el prompt
MIN_ADVICE = 10  # resueltas válidas antes de dar consejos al modelo


class PracticeError(RuntimeError):
    """La pasada de práctica no pudo completarse."""


@dataclass
class PracticeReport:
    predicted: list[tuple[Market, Forecast]] = field(default_factory=list)
    resolved: list[ResolvedForecast] = field(default_factory=list)
    advice: str | None = None
    reliability_oraqlo: float | None = None
    reliability_market: float | None = None
    errors: list[str] = field(default_factory=list)
    offline: bool = False  # algún paso falló por falta de conexión
    market_status: dict[str, str] = field(default_factory=dict)


def market_tag(market_id: str) -> str:
    return f"[polymarket:{market_id}]"


def extract_market_id(question: str) -> str | None:
    match = _TAG_RE.match(question)
    return match.group(1) if match else None


def calibration_advice(
    ledger: CalibrationLedger,
    source: str,
    benchmark_source: str = MARKET_SOURCE,
    min_resolved: int = MIN_ADVICE,
    recalibrated: bool = False,
) -> str | None:
    """Consejo de calibración a partir del historial resuelto de `source`.

    Solo con previsiones que cuentan (sin preguntas ya sabidas) y desde `min_resolved`:
    con 3 casos un «sobreconfías» es ruido. Compara con el mercado en las MISMAS
    preguntas y mide la confianza sobre las probabilidades brutas (antes de recalibrar).
    Si la recalibración ya está corrigiendo (`recalibrated`), no pide al modelo que
    concentre o reparta: las dos correcciones se perseguirían la una a la otra.
    """
    decided = decided_markets(
        [*ledger.open_forecasts, *(r.forecast for r in ledger.resolved_forecasts)]
    )
    resolved = [
        r
        for r in ledger.resolved_forecasts
        if r.forecast.source == source and counts_for_score(r.forecast, decided)
    ]
    if len(resolved) < min_resolved:
        return None

    mean_brier = sum(r.brier for r in resolved) / len(resolved)
    parts = [
        f"En tus últimas {len(resolved)} predicciones resueltas tu Brier medio es "
        f"{mean_brier:.3f} (0 = perfecto)."
    ]

    mids = {market_id_of(r.forecast.question) for r in resolved} - {None}
    bench = [
        r.brier
        for r in ledger.resolved_forecasts
        if r.forecast.source == benchmark_source and market_id_of(r.forecast.question) in mids
    ]
    if bench:
        market = sum(bench) / len(bench)
        comparison = "mejor" if mean_brier < market else "peor"
        parts.append(
            f"El mercado, en esas mismas preguntas, tiene Brier {market:.3f}: vas {comparison} que él."
        )
    if recalibrated:
        return " ".join(parts)

    raws = [(raw_of(r.forecast), r) for r in resolved]
    confident = [(o, r) for o, r in raws if o and max(o.values()) >= 0.7]
    if len(confident) >= 5:
        hits = sum(1 for o, r in confident if max(o, key=lambda k: o[k]) == r.actual_outcome)
        rate = hits / len(confident)
        avg_conf = sum(max(o.values()) for o, _r in confident) / len(confident)
        if rate < avg_conf - 0.1:
            parts.append(
                f"Cuando afirmas con ≥70% de confianza solo aciertas el {rate:.0%} "
                f"(prometías {avg_conf:.0%}): SOBRECONFÍAS — reparte más la probabilidad."
            )
        elif rate > avg_conf + 0.1:
            parts.append(
                f"Cuando afirmas con ≥70% aciertas el {rate:.0%} (prometías {avg_conf:.0%}): "
                f"SUBCONFÍAS — atrévete a concentrar más probabilidad."
            )
    return " ".join(parts)


PICK_POOL = 60  # mercados abiertos entre los que se elige
INFORMATIVE = 0.9  # con el favorito por encima, el duelo enseña poco


def _event_key(market: Market) -> str:
    events = (market.raw or {}).get("events") or []
    if events and isinstance(events[0], dict):
        return str(events[0].get("slug") or events[0].get("id") or market.id)
    return str((market.raw or {}).get("slug") or market.id)


MAX_GAMES_PER_RUN = 1  # partidos por pasada: el resto, de otros temas


def is_game(market: Market) -> bool:
    """Un partido o evento deportivo (Polymarket les pone hora de comienzo o tipo)."""
    raw = market.raw or {}
    return bool(raw.get("gameStartTime") or raw.get("sportsMarketType"))


def choose_markets(markets: list[Market], n: int) -> list[Market]:
    """Los `n` mercados que más enseñan: primero los inciertos (favorito ≤ 90%), uno por
    evento (no cinco líneas del mismo partido) y, a igualdad, los de más volumen.

    Como mucho MAX_GAMES_PER_RUN partidos por pasada: Polymarket está lleno de partidos
    de tenis poco conocidos y, sin límite, la Práctica aprendía casi solo de eso, que no
    se parece a lo que se le pregunta a Oraqlo. Si no hay otra cosa, se rellena con
    partidos: nunca se queda sin práctica."""
    ranked = sorted(
        markets,
        key=lambda m: (max(m.outcomes.values(), default=1.0) > INFORMATIVE, -m.volume_24h),
    )
    chosen: list[Market] = []
    events: set[str] = set()
    games = 0
    for strict in (True, False):
        for m in ranked:
            if len(chosen) >= n:
                return chosen
            key = _event_key(m)
            if key in events:
                continue
            if strict and is_game(m) and games >= MAX_GAMES_PER_RUN:
                continue
            chosen.append(m)
            events.add(key)
            games += is_game(m)
    return chosen


class PracticeEngine:
    def __init__(
        self,
        market_client: PolymarketClient,
        provider: LLMProvider,
        model: str,
        ledger: CalibrationLedger,
        researcher: Researcher | None = None,
        markets_per_run: int = 2,
        members: list[Member] | None = None,
        weights: dict[str, float] | None = None,
    ) -> None:
        self.market_client = market_client
        self.provider = provider
        self.model = model
        self.ledger = ledger
        self.researcher = researcher
        self.markets_per_run = markets_per_run
        # Quién predice: el comité (varios modelos) o el modelo del rol, una vez.
        self.members = members or build_members((provider, model), [], 1)
        self.weights = weights
        self.committee = len({m.model for m in self.members}) > 1
        # Con comité, la previsión combinada aprende su propia calibración.
        self.source_model = COMMITTEE_MODEL if self.committee else model
        self.oraqlo_source = f"llm:{self.source_model}"
        # Fase en Polymarket de los mercados vencidos sin resolver (la llena resolve_due).
        self.market_status: dict[str, str] = {}

    # ── Predicción ────────────────────────────────────────────────────────

    def _known_market_ids(self) -> set[str]:
        ids = set()
        for f in self.ledger.open_forecasts:
            mid = extract_market_id(f.question)
            if mid:
                ids.add(mid)
        for r in self.ledger.resolved_forecasts:
            mid = extract_market_id(r.forecast.question)
            if mid:
                ids.add(mid)
        return ids

    def _horizon(self, market: Market) -> timedelta:
        if market.end_date is None:
            return timedelta(days=7)
        delta = market.end_date - datetime.now(UTC)
        return max(delta, timedelta(days=1))

    def _ask_member(
        self, member: Member, system: str, user: str, labels: list[str]
    ) -> tuple[dict[str, float], dict]:
        try:
            data = chat_json(
                member.provider, member.model, system, f"{user}\n\n{member.lens}", temperature=0.2
            )
        except JsonChatError as e:
            raise PracticeError(str(e)) from e
        raw = data.get("outcomes", {})
        by_norm = {" ".join(str(k).split()).casefold(): v for k, v in raw.items()}
        try:
            probs = {
                label: float(
                    raw[label] if label in raw else by_norm[" ".join(label.split()).casefold()]
                )
                for label in labels
            }
        except (KeyError, TypeError, ValueError) as e:
            raise PracticeError(
                f"{member.model} no dio probabilidad para todas las etiquetas {labels}: {e}"
            ) from e
        total = sum(probs.values())
        if total <= 0 or any(v < 0 for v in probs.values()):
            raise PracticeError(f"{member.model}: probabilidades no válidas.")
        return {k: v / total for k, v in probs.items()}, data

    def _forecast_market(
        self,
        market: Market,
        advice: str | None,
        context: str | None,
        recal: Recalibration | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[Forecast, list[Forecast]]:
        """(la previsión de Oraqlo, lo que dijo cada modelo del comité)."""
        system = _FORECAST_PROMPT
        if advice:
            system += f"\n\nTu historial de calibración dice: {advice}"
        labels = list(market.outcomes)
        user = (
            f"Fecha de hoy: {date.today().isoformat()}\n"
            f"Pregunta: {market.question}\n"
            f"Resuelve el: {market.end_date.date().isoformat() if market.end_date else 'n/d'}\n"
            f"Resultados posibles (etiquetas exactas): {labels}\n"
            "NO te doy el precio del mercado: estima tú."
        )
        rules = market.rules[:RULES_CHARS].strip()
        if rules:
            user += f"\n\nReglas de resolución:\n{rules}"
        if context:
            user += f"\n\n{context}"
        errors: list[str] = []

        def failed(member: Member, e: Exception) -> None:
            errors.append(str(e))
            if progress:
                progress(f"  {member.model} no respondió ({e}); sigo sin él")

        answers = run_members(
            self.members, lambda m: self._ask_member(m, system, user, labels), on_error=failed
        )
        if not answers:
            raise PracticeError(errors[0] if errors else "Ningún modelo respondió.")
        outcomes, per_model = combine(
            [(m.model, dist) for m, (dist, _data) in answers], self.weights
        )
        data = answers[0][1][1]
        assumptions = [str(a) for a in data.get("assumptions", [])]
        if not context:  # sin investigación: solo intuición
            bases = [
                b
                for _m, (_d, d) in answers
                if (b := parse_base_rate(d.get("base_rate_outcomes"), labels)) is not None
            ]
            base = pool_log_odds(bases) if bases else None
            outcomes = temper_without_data(outcomes, base)
            assumptions.append(NO_DATA_BASE_NOTE if base else NO_DATA_NOTE)
        base_rate = str(data.get("base_rate") or "").strip()
        if base_rate:
            assumptions.append(f"Tasa base: {base_rate}")
        if self.committee:
            assumptions.append(weights_note(self.weights or {}, per_model))
        group = group_key("practica", len(outcomes), self._horizon(market).days)
        if recal is not None and recal.active_for(group):
            outcomes = clamp(recal.apply(outcomes, group))
            assumptions.append(recal.note(group))
        distribution = Distribution(outcomes=outcomes)
        distribution.validate()
        now = datetime.now(UTC)
        question = f"{market_tag(market.id)} {market.question}"
        forecast = Forecast(
            id=str(uuid.uuid4()),
            question=question,
            distribution=distribution,
            horizon=self._horizon(market),
            assumptions=assumptions,
            source=self.oraqlo_source,
            created_at=now,
        )
        members = []
        if self.committee:
            members = [
                Forecast(
                    id=f"{forecast.id}{MEMBER_SEP}{model}",
                    question=question,
                    distribution=Distribution(outcomes=dist),
                    horizon=forecast.horizon,
                    assumptions=["Lo que dijo este modelo del comité, antes de combinar."],
                    source=member_source(model),
                    created_at=now,
                )
                for model, dist in per_model.items()
            ]
        return forecast, members

    def _benchmark_forecast(self, market: Market) -> Forecast:
        total = sum(market.outcomes.values()) or 1.0
        distribution = Distribution(outcomes={k: v / total for k, v in market.outcomes.items()})
        return Forecast(
            id=str(uuid.uuid4()),
            question=f"{market_tag(market.id)} {market.question}",
            distribution=distribution,
            horizon=self._horizon(market),
            assumptions=["precio del mercado en el momento de la predicción de Oraqlo"],
            source=MARKET_SOURCE,
            created_at=datetime.now(UTC),
        )

    def predict_new(
        self,
        n: int | None = None,
        research: bool = True,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[tuple[Market, Forecast]]:
        progress = on_progress or (lambda msg: None)
        n = n or self.markets_per_run
        recal = for_model(self.ledger, self.source_model)
        if recal.active:
            progress(f"recalibración activa (a={recal.a:.2f}): {recal.describe()}")
        advice = calibration_advice(self.ledger, self.oraqlo_source, recalibrated=recal.active)
        if advice:
            progress(f"consejo de calibración activo: {advice}")
        if self.committee:
            progress("comité: " + ", ".join(dict.fromkeys(m.model for m in self.members)))

        known = self._known_market_ids()
        candidates = choose_markets(
            [
                m
                for m in self.market_client.list_open(limit=PICK_POOL + len(known))
                if m.id not in known
            ],
            n,
        )
        if not candidates:
            progress("sin mercados nuevos que predecir")
            return []

        predicted: list[tuple[Market, Forecast]] = []
        for market in candidates:
            progress(f"prediciendo: {market.question}")
            context = None
            if research and self.researcher is not None:
                try:
                    brief = self.researcher.investigate(market.question, self._horizon(market))
                    context = brief.as_context()
                    progress(
                        f"  investigado: {len(brief.facts)} hechos de {len(brief.sources)} fuentes"
                    )
                except (ResearchError, Exception) as e:  # la práctica no se detiene por esto
                    progress(f"  investigación no disponible ({e}); prediciendo sin ella")
            forecast, members = self._forecast_market(market, advice, context, recal, progress)
            self.ledger.record(forecast)
            self.ledger.record(self._benchmark_forecast(market))
            for member in members:
                self.ledger.record(member)
            top = forecast.distribution.mode()
            progress(
                f"  Oraqlo: {forecast.distribution.outcomes[top]:.0%} '{top}' · "
                f"mercado: {market.outcomes.get(top, 0):.0%}"
            )
            predicted.append((market, forecast))
        return predicted

    # ── Resolución ────────────────────────────────────────────────────────

    def _fetch_markets(
        self, ids: list[str], due: set[str], progress: Callable[[str], None]
    ) -> dict[str, Market]:
        """Los mercados a revisar: por lotes (solo los cerrados, y de los vencidos aún
        abiertos su fase en UMA) o, si el cliente no sabe o el lote falla, uno a uno."""
        batch = getattr(self.market_client, "get_markets", None)
        if batch is not None:
            try:
                found = batch(ids, closed=True)
                pending = [mid for mid in ids if mid in due and mid not in found]
                if pending:
                    found.update(batch(pending, closed=False))
                return found
            except PolymarketError as e:
                if e.offline:
                    raise  # sin red, uno a uno tampoco va a funcionar
                progress(f"consulta por lotes no disponible ({e}); se consulta uno a uno")
        found = {}
        for mid in ids:
            try:
                found[mid] = self.market_client.get_market(mid)
            except PolymarketError as e:
                if e.offline:
                    raise
                progress(f"mercado {mid}: no consultable ({e})")
        return found

    def resolve_due(
        self, on_progress: Callable[[str], None] | None = None
    ) -> list[ResolvedForecast]:
        """Cierra las previsiones de mercados ya resueltos en Polymarket y descarta las de
        mercados anulados. De los vencidos que siguen sin resolver deja su fase en UMA en
        `self.market_status` ({id: "pendiente" | "proposed" | "disputed"})."""
        progress = on_progress or (lambda msg: None)
        now = datetime.now(UTC)
        by_market: dict[str, list[Forecast]] = {}
        due: set[str] = set()
        for f in self.ledger.open_forecasts:
            mid = extract_market_id(f.question)
            if mid:
                by_market.setdefault(mid, []).append(f)
                if f.created_at and f.created_at + f.horizon <= now:
                    due.add(mid)

        markets = self._fetch_markets(list(by_market), due, progress)
        resolved: list[ResolvedForecast] = []
        self.market_status = {}
        for mid, forecasts in by_market.items():
            market = markets.get(mid)
            if market is None or not market.resolved:
                if mid in due:
                    status = market.uma_status if market else None
                    self.market_status[mid] = status or "pendiente"
                continue
            winner = market.winner
            if winner is None:  # anulado (p. ej. 0.5/0.5): nunca tendrá ganador
                for f in forecasts:
                    self.ledger.discard(f.id)
                progress(f"anulado en Polymarket, se descarta: '{market.question}'")
                continue
            for f in forecasts:
                r = self.ledger.resolve(f.id, winner)
                resolved.append(r)
                progress(
                    f"resuelto [{f.source}] '{market.question}' → "
                    f"'{market.winner}' (Brier {r.brier:.3f})"
                )
        return resolved

    # ── Pasada completa ───────────────────────────────────────────────────

    def run_once(
        self, research: bool = True, on_progress: Callable[[str], None] | None = None
    ) -> PracticeReport:
        report = PracticeReport()
        try:
            report.resolved = self.resolve_due(on_progress)
            report.market_status = dict(self.market_status)
        except PolymarketError as e:
            report.errors.append(str(e))
            report.offline |= e.offline
        if report.offline:
            return report  # sin red no hay mercados nuevos que pedir
        try:
            report.predicted = self.predict_new(research=research, on_progress=on_progress)
        except (PolymarketError, PracticeError) as e:
            report.errors.append(str(e))
            report.offline |= getattr(e, "offline", False)
        report.advice = calibration_advice(self.ledger, self.oraqlo_source)
        report.reliability_oraqlo = self.ledger.reliability(self.oraqlo_source)
        report.reliability_market = self.ledger.reliability(MARKET_SOURCE)
        return report


def main(argv: list[str] | None = None) -> int:
    """CLI headless (`oraqlo practice`): una pasada o un bucle, sobre la base de Oraqlo.

    Usa el rol `practica` de oraqlo.toml y el mismo ledger SQLite que la app web.
    Con el servidor en marcha no hace falta: la práctica autónoma corre en él.
    """
    import argparse
    import time as _time

    from oraqlo.services.core import OraqloService

    parser = argparse.ArgumentParser(
        prog="oraqlo practice",
        description="Práctica autónoma de Oraqlo con Polymarket (solo lectura).",
    )
    parser.add_argument("--markets", type=int, default=None, help="mercados nuevos por pasada")
    parser.add_argument("--no-research", action="store_true", help="predecir sin investigación web")
    parser.add_argument("--loop", action="store_true", help="repetir indefinidamente")
    parser.add_argument("--interval-min", type=float, default=60.0, help="minutos entre pasadas")
    args = parser.parse_args(argv)

    service = OraqloService()
    if args.markets is not None:
        service.config.practice.markets = args.markets
    log = lambda kind, data: print(data.get("msg", "")) if kind == "log" else None

    while True:
        print(f"── pasada de práctica · {datetime.now().strftime('%Y-%m-%d %H:%M')} ──")
        result = service.practice(research=not args.no_research, emit=log)
        print(f"predichas: {len(result['predicted'])} · resueltas: {len(result['resolved'])}")
        if result["reliability_oraqlo"] is not None:
            market = result["reliability_market"]
            bench = f" · mercado {market:.3f}" if market is not None else ""
            print(f"Brier Oraqlo {result['reliability_oraqlo']:.3f}{bench}")
        for error in result["errors"]:
            print(f"error: {error}")
        if not args.loop:
            return 0
        _time.sleep(args.interval_min * 60)


if __name__ == "__main__":
    raise SystemExit(main())


# ── duelos: Oraqlo frente al mercado, pregunta a pregunta ─────────────────

TIE = 0.005  # diferencias de Brier menores son un empate


def strip_tag(question: str) -> str:
    return _TAG_RE.sub("", question, count=1).strip()


def _focus_label(oraqlo: Forecast, market: Forecast, actual: str | None) -> str:
    """El resultado que se compara: el que ganó o, si sigue abierto, el «sí»."""
    if actual:
        return actual
    labels = list(market.distribution.outcomes) or list(oraqlo.distribution.outcomes)
    for label in labels:
        if label.strip().lower() in ("yes", "sí", "si"):
            return label
    return max(labels, key=lambda k: market.distribution.outcomes.get(k, 0.0)) if labels else ""


def _skill(brier: float | None, chance: float | None) -> float | None:
    return 1 - brier / chance if brier is not None and chance else None


def duels(ledger: CalibrationLedger) -> dict:
    """Empareja la previsión de Oraqlo y la del mercado de cada pregunta (por la
    etiqueta del mercado) y dice quién se acercó más. Las que no tienen pareja
    (p. ej. una pasada que falló a medias) se ignoran."""
    from oraqlo.memory.scorecard import chance_brier

    sides: dict[str, dict[str, tuple[Forecast, ResolvedForecast | None]]] = {}

    def add(f: Forecast, r: ResolvedForecast | None) -> None:
        mid = extract_market_id(f.question)
        if mid and not is_member(f.source):
            side = "market" if f.source == MARKET_SOURCE else "oraqlo"
            sides.setdefault(mid, {})[side] = (f, r)

    for f in ledger.open_forecasts:
        add(f, None)
    for r in ledger.resolved_forecasts:
        add(r.forecast, r)

    items: list[dict] = []
    for mid, pair in sides.items():
        if "oraqlo" not in pair or "market" not in pair:
            continue
        (fo, ro), (fm, rm) = pair["oraqlo"], pair["market"]
        done = ro is not None and rm is not None
        decided = max(fm.distribution.outcomes.values(), default=0.0) >= DECIDED
        actual = ro.actual_outcome if ro is not None and done else None
        label = _focus_label(fo, fm, actual)
        item = {
            "market_id": mid,
            "question": strip_tag(fo.question),
            "label": label,
            "oraqlo_p": fo.distribution.outcomes.get(label, 0.0),
            "market_p": fm.distribution.outcomes.get(label, 0.0),
            "created_at": fo.created_at.isoformat() if fo.created_at else None,
            "closes_at": (fo.created_at + fo.horizon).isoformat() if fo.created_at else None,
            "status": "resuelto" if done else "abierto",
            "actual": actual,
            "resolved_at": ro.resolved_at.isoformat() if done and ro else None,
            "oraqlo_brier": ro.brier if done and ro else None,
            "market_brier": rm.brier if done and rm else None,
            "chance_brier": chance_brier(ro) if done and ro else None,
            "winner": None,
            "already_decided": decided,
        }
        if done and ro and rm and not decided:
            diff = ro.brier - rm.brier
            item["winner"] = "empate" if abs(diff) < TIE else ("oraqlo" if diff < 0 else "mercado")
        items.append(item)

    items.sort(key=lambda d: d["resolved_at"] or d["created_at"] or "", reverse=True)
    resolved = [d for d in items if d["status"] == "resuelto" and not d["already_decided"]]

    def mean(key: str) -> float | None:
        return sum(d[key] for d in resolved) / len(resolved) if resolved else None

    return {
        "items": items,
        "summary": {
            "resolved": len(resolved),
            "open": sum(d["status"] == "abierto" for d in items),
            "already_decided": sum(d["already_decided"] for d in items),
            "won": sum(d["winner"] == "oraqlo" for d in resolved),
            "lost": sum(d["winner"] == "mercado" for d in resolved),
            "tied": sum(d["winner"] == "empate" for d in resolved),
            "oraqlo_brier": mean("oraqlo_brier"),
            "market_brier": mean("market_brier"),
            "chance_brier": mean("chance_brier"),
            # «% mejor que el azar», la misma escala que Calibración.
            "oraqlo_skill": _skill(mean("oraqlo_brier"), mean("chance_brier")),
            "market_skill": _skill(mean("market_brier"), mean("chance_brier")),
        },
    }
