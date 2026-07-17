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
from datetime import date, datetime, timedelta, timezone

from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.llm.base import LLMProvider
from oraqlo.llm.jsonchat import JsonChatError, chat_json
from oraqlo.memory.calibration import CalibrationLedger, ResolvedForecast
from oraqlo.polymarket import Market, PolymarketClient, PolymarketError
from oraqlo.research import Researcher, ResearchError

MARKET_SOURCE = "polymarket:mercado"
_TAG_RE = re.compile(r"^\[polymarket:(\d+)\]")

_FORECAST_PROMPT = (
    "Eres el Oráculo de Oraqlo: probabilidades calibradas, nunca certezas. "
    "Asigna probabilidad a CADA UNO de los resultados dados, usando EXACTAMENTE "
    "esas etiquetas (ni una más, ni una menos); deben sumar 1.0. Responde SOLO "
    'con JSON: {"outcomes": {"<etiqueta>": <prob>, ...}, '
    '"assumptions": ["<supuesto>", ...]}'
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
    ledger: CalibrationLedger, source: str, benchmark_source: str = MARKET_SOURCE,
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
    parts = [f"En tus últimas {len(resolved)} predicciones resueltas tu Brier medio es "
             f"{mean_brier:.3f} (0 = perfecto)."]

    bench = ledger.reliability(benchmark_source)
    if bench is not None:
        comparison = "mejor" if mean_brier < bench else "peor"
        parts.append(f"El mercado (multitud) tiene Brier {bench:.3f}: vas {comparison} que él.")

    confident = [
        r for r in resolved
        if max(r.forecast.distribution.outcomes.values()) >= 0.7
    ]
    if len(confident) >= 3:
        hits = sum(
            1 for r in confident
            if max(r.forecast.distribution.outcomes, key=r.forecast.distribution.outcomes.get)
            == r.actual_outcome
        )
        rate = hits / len(confident)
        avg_conf = sum(max(r.forecast.distribution.outcomes.values()) for r in confident) / len(confident)
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
        delta = market.end_date - datetime.now(timezone.utc)
        return max(delta, timedelta(days=1))

    def _forecast_market(self, market: Market, advice: str | None,
                         context: str | None) -> Forecast:
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
        distribution = Distribution(outcomes={k: v / total for k, v in probs.items()})
        distribution.validate()
        return Forecast(
            id=str(uuid.uuid4()),
            question=f"{market_tag(market.id)} {market.question}",
            distribution=distribution,
            horizon=self._horizon(market),
            assumptions=[str(a) for a in data.get("assumptions", [])],
            source=self.oraqlo_source,
            created_at=datetime.now(timezone.utc),
        )

    def _benchmark_forecast(self, market: Market) -> Forecast:
        total = sum(market.outcomes.values()) or 1.0
        distribution = Distribution(
            outcomes={k: v / total for k, v in market.outcomes.items()}
        )
        return Forecast(
            id=str(uuid.uuid4()),
            question=f"{market_tag(market.id)} {market.question}",
            distribution=distribution,
            horizon=self._horizon(market),
            assumptions=["precio del mercado en el momento de la predicción de Oraqlo"],
            source=MARKET_SOURCE,
            created_at=datetime.now(timezone.utc),
        )

    def predict_new(
        self, n: int | None = None, research: bool = True,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[tuple[Market, Forecast]]:
        progress = on_progress or (lambda msg: None)
        n = n or self.markets_per_run
        advice = calibration_advice(self.ledger, self.oraqlo_source)
        if advice:
            progress(f"consejo de calibración activo: {advice}")

        known = self._known_market_ids()
        candidates = [m for m in self.market_client.list_open(limit=n * 3 + len(known))
                      if m.id not in known][:n]
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
                    progress(f"  investigado: {len(brief.facts)} hechos de {len(brief.sources)} fuentes")
                except (ResearchError, Exception) as e:  # la práctica no se detiene por esto
                    progress(f"  investigación no disponible ({e}); prediciendo sin ella")
            forecast = self._forecast_market(market, advice, context)
            self.ledger.record(forecast)
            self.ledger.record(self._benchmark_forecast(market))
            top = max(forecast.distribution.outcomes, key=forecast.distribution.outcomes.get)
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
                progress(f"resuelto [{f.source}] '{market.question}' → "
                         f"'{market.winner}' (Brier {r.brier:.3f})")
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
    """CLI headless: una pasada (o bucle) de práctica autónoma."""
    import argparse
    import sys
    import time as _time

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="python -m oraqlo.autopractice",
        description="Práctica autónoma de Oraqlo con Polymarket (solo lectura).",
    )
    parser.add_argument("--markets", type=int, default=2, help="mercados nuevos por pasada")
    parser.add_argument("--no-research", action="store_true", help="predecir sin investigación web")
    parser.add_argument("--loop", action="store_true", help="repetir indefinidamente")
    parser.add_argument("--interval-min", type=float, default=60.0, help="minutos entre pasadas")
    parser.add_argument("--ledger", default="oraqlo_ledger.json", help="ruta del ledger")
    parser.add_argument("--backend", choices=["cloud", "local"], default="cloud")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    from oraqlo.llm.ollama import OllamaBackendConfig, OllamaProvider
    from oraqlo.llm.websearch import OllamaWebClient
    from oraqlo.memory.store import JsonLedger

    config = OllamaBackendConfig.cloud() if args.backend == "cloud" else OllamaBackendConfig.local()
    model = args.model or ("gpt-oss:120b-cloud" if args.backend == "cloud" else "llama3.1")
    provider = OllamaProvider(config)
    web = OllamaWebClient()
    researcher = Researcher(web, provider, model) if web.is_available() else None

    engine = PracticeEngine(
        PolymarketClient(), provider, model, JsonLedger(args.ledger),
        researcher=researcher, markets_per_run=args.markets,
    )

    while True:
        print(f"── pasada de práctica · {datetime.now().strftime('%Y-%m-%d %H:%M')} ──")
        report = engine.run_once(research=not args.no_research, on_progress=print)
        print(f"predichas: {len(report.predicted)} · resueltas: {len(report.resolved)}")
        if report.reliability_oraqlo is not None:
            bench = (f" · mercado {report.reliability_market:.3f}"
                     if report.reliability_market is not None else "")
            print(f"Brier Oraqlo {report.reliability_oraqlo:.3f}{bench}")
        for error in report.errors:
            print(f"error: {error}")
        if not args.loop:
            return 1 if report.errors and not report.predicted and not report.resolved else 0
        _time.sleep(args.interval_min * 60)


if __name__ == "__main__":
    raise SystemExit(main())
