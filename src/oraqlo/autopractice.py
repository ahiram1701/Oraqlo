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

from oraqlo.forecaster.aggregate import NO_DATA_NOTE, clamp, temper_without_data
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.llm.base import LLMProvider
from oraqlo.llm.jsonchat import JsonChatError, chat_json
from oraqlo.memory.calibration import CalibrationLedger, ResolvedForecast
from oraqlo.memory.recalibration import (
    DECIDED,
    MARKET_SOURCE,
    Recalibration,
    for_model,
)
from oraqlo.polymarket import Market, PolymarketClient, PolymarketError
from oraqlo.research import Researcher, ResearchError

_TAG_RE = re.compile(r"^\[polymarket:(\d+)\]")

_FORECAST_PROMPT = (
    "Eres el Oráculo de Oraqlo: probabilidades calibradas, nunca certezas. "
    "Asigna probabilidad a CADA UNO de los resultados dados, usando EXACTAMENTE "
    "esas etiquetas (ni una más, ni una menos); deben sumar 1.0. Empieza por la "
    "tasa base (en casos parecidos, con qué frecuencia pasa cada cosa), ajústala con "
    "lo específico y evita 0 y 1. Si no tienes datos concretos de ESTE caso (ranking, forma, cuotas, encuestas, cifras recientes), quédate en la tasa base o cerca del reparto igual: una ventaja que solo intuyes no pasa del 55-60%. Responde SOLO con JSON: "
    '{"base_rate": "<clase de referencia y frecuencia>", '
    '"outcomes": {"<etiqueta>": <prob>, ...}, "assumptions": ["<supuesto>", ...]}'
)


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


def market_tag(market_id: str) -> str:
    return f"[polymarket:{market_id}]"


def extract_market_id(question: str) -> str | None:
    match = _TAG_RE.match(question)
    return match.group(1) if match else None


def calibration_advice(
    ledger: CalibrationLedger,
    source: str,
    benchmark_source: str = MARKET_SOURCE,
    min_resolved: int = 3,
) -> str | None:
    """Consejo de calibración a partir del historial resuelto de `source`.

    Mide Brier medio (vs. el benchmark si existe) y el acierto real cuando la
    predicción fue confiada (top ≥ 70%): la sobre/subconfianza es lo más
    corregible por prompt.
    """
    resolved = [r for r in ledger.resolved_forecasts if r.forecast.source == source]
    if len(resolved) < min_resolved:
        return None

    briers = [r.brier for r in resolved]
    mean_brier = sum(briers) / len(briers)
    parts = [
        f"En tus últimas {len(resolved)} predicciones resueltas tu Brier medio es "
        f"{mean_brier:.3f} (0 = perfecto)."
    ]

    bench = ledger.reliability(benchmark_source)
    if bench is not None:
        comparison = "mejor" if mean_brier < bench else "peor"
        parts.append(f"El mercado (multitud) tiene Brier {bench:.3f}: vas {comparison} que él.")

    confident = [r for r in resolved if max(r.forecast.distribution.outcomes.values()) >= 0.7]
    if len(confident) >= 3:
        hits = sum(1 for r in confident if r.forecast.distribution.mode() == r.actual_outcome)
        rate = hits / len(confident)
        avg_conf = sum(max(r.forecast.distribution.outcomes.values()) for r in confident) / len(
            confident
        )
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


class PracticeEngine:
    def __init__(
        self,
        market_client: PolymarketClient,
        provider: LLMProvider,
        model: str,
        ledger: CalibrationLedger,
        researcher: Researcher | None = None,
        markets_per_run: int = 2,
    ) -> None:
        self.market_client = market_client
        self.provider = provider
        self.model = model
        self.ledger = ledger
        self.researcher = researcher
        self.markets_per_run = markets_per_run
        self.oraqlo_source = f"llm:{model}"

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

    def _forecast_market(
        self,
        market: Market,
        advice: str | None,
        context: str | None,
        recal: Recalibration | None = None,
    ) -> Forecast:
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
        if context:
            user += f"\n\n{context}"
        try:
            data = chat_json(self.provider, self.model, system, user, temperature=0.2)
        except JsonChatError as e:
            raise PracticeError(str(e)) from e
        raw = data.get("outcomes", {})
        try:
            probs = {label: float(raw[label]) for label in labels}
        except (KeyError, TypeError, ValueError) as e:
            raise PracticeError(
                f"El oráculo no dio probabilidad para todas las etiquetas {labels}: {e}"
            ) from e
        total = sum(probs.values())
        if total <= 0:
            raise PracticeError("Probabilidades no positivas.")
        outcomes = clamp({k: v / total for k, v in probs.items()})
        assumptions = [str(a) for a in data.get("assumptions", [])]
        if not context:  # sin investigación: solo intuición
            outcomes = temper_without_data(outcomes)
            assumptions.append(NO_DATA_NOTE)
        base_rate = str(data.get("base_rate") or "").strip()
        if base_rate:
            assumptions.append(f"Tasa base: {base_rate}")
        if recal is not None and recal.active:
            outcomes = clamp(recal.apply(outcomes))
            assumptions.append(recal.note())
        distribution = Distribution(outcomes=outcomes)
        distribution.validate()
        return Forecast(
            id=str(uuid.uuid4()),
            question=f"{market_tag(market.id)} {market.question}",
            distribution=distribution,
            horizon=self._horizon(market),
            assumptions=assumptions,
            source=self.oraqlo_source,
            created_at=datetime.now(UTC),
        )

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
        advice = calibration_advice(self.ledger, self.oraqlo_source)
        if advice:
            progress(f"consejo de calibración activo: {advice}")
        recal = for_model(self.ledger, self.model)
        if recal.active:
            progress(f"recalibración activa (a={recal.a:.2f}): {recal.describe()}")

        known = self._known_market_ids()
        candidates = [
            m for m in self.market_client.list_open(limit=n * 3 + len(known)) if m.id not in known
        ][:n]
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
            forecast = self._forecast_market(market, advice, context, recal)
            self.ledger.record(forecast)
            self.ledger.record(self._benchmark_forecast(market))
            top = forecast.distribution.mode()
            progress(
                f"  Oraqlo: {forecast.distribution.outcomes[top]:.0%} '{top}' · "
                f"mercado: {market.outcomes.get(top, 0):.0%}"
            )
            predicted.append((market, forecast))
        return predicted

    # ── Resolución ────────────────────────────────────────────────────────

    def resolve_due(
        self, on_progress: Callable[[str], None] | None = None
    ) -> list[ResolvedForecast]:
        progress = on_progress or (lambda msg: None)
        by_market: dict[str, list[Forecast]] = {}
        for f in self.ledger.open_forecasts:
            mid = extract_market_id(f.question)
            if mid:
                by_market.setdefault(mid, []).append(f)

        resolved: list[ResolvedForecast] = []
        for mid, forecasts in by_market.items():
            try:
                market = self.market_client.get_market(mid)
            except PolymarketError as e:
                progress(f"mercado {mid}: no consultable ({e})")
                continue
            if not market.resolved or market.winner is None:
                continue
            for f in forecasts:
                r = self.ledger.resolve(f.id, market.winner)
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
        except PolymarketError as e:
            report.errors.append(str(e))
        try:
            report.predicted = self.predict_new(research=research, on_progress=on_progress)
        except (PolymarketError, PracticeError) as e:
            report.errors.append(str(e))
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
        if mid:
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
