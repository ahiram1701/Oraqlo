import { Link } from "react-router-dom";
import { pct } from "../lib/api";
import { Icon } from "./icons";
import { ProbBars } from "./ProbBars";
import { Badge, VerdictBadge, cx, plainProbability, verdictInfo } from "./ui";

function topOutcome(outcomes: Record<string, number>): [string, number] | null {
  const sorted = Object.entries(outcomes ?? {}).sort((a, b) => b[1] - a[1]);
  return sorted[0] ?? null;
}

/** Cuándo se sabrá: fecha de la previsión + horizonte. */
export function resolvesOn(forecast: any): string | null {
  const days = Number(forecast?.horizon_days);
  if (!Number.isFinite(days) || days <= 0) return null;
  const start = forecast?.created_at ? new Date(forecast.created_at) : new Date();
  if (Number.isNaN(start.getTime())) return null;
  const end = new Date(start.getTime() + days * 86_400_000);
  return end.toLocaleDateString("es", { day: "numeric", month: "long", year: "numeric" });
}

/** Lo que más explica la previsión primero: tasa base y razonamiento. */
export function rankAssumptions(assumptions: string[]): string[] {
  const weight = (a: string) => (a.startsWith("Tasa base") ? 0 : a.startsWith("Razonamiento") ? 1 : 2);
  return [...assumptions].sort((a, b) => weight(a) - weight(b));
}

const num = (x: number) => x.toLocaleString("es", { maximumFractionDigits: 2 });

function confidenceWords(c: number) {
  return c >= 0.8 ? "alta" : c >= 0.6 ? "media" : "baja";
}

/** Texto plano del resultado, para compartirlo. */
export function resultText(question: string, result: any): string {
  if (result?.kind === "goal") {
    return `${question}\nProbabilidad de lograrlo: ${pct(result.baseline_p)} → ${pct(result.with_plan_p)} con el plan.\n${result.plan?.summary ?? ""}`;
  }
  if (result?.kind === "cycle") {
    const v = verdictInfo(result.verdict?.decision);
    return `${question}\n${v.label}: ${result.recommendation?.action?.description ?? ""}\n${result.verdict?.rationale ?? ""}`;
  }
  const top = topOutcome(result?.forecast?.outcomes ?? {});
  const lines = Object.entries(result?.forecast?.outcomes ?? {})
    .sort((a: any, b: any) => b[1] - a[1])
    .map(([k, v]) => `• ${k}: ${pct(v as number)}`);
  return [question, top ? `Lo más probable: ${top[0]} (${pct(top[1])})` : "", ...lines].filter(Boolean).join("\n");
}

/** La respuesta, arriba y grande; los detalles, debajo. */
export function ResultCard({ result }: { result: any }) {
  if (result?.kind === "goal") {
    const up = result.uplift as number;
    return (
      <div className="result-glow -mx-4 -mt-4 space-y-4 rounded-t-2xl px-4 pt-5 pb-1 sm:-mx-5 sm:-mt-5 sm:px-5">
        <p className="text-sm text-muted">Probabilidad de lograrlo</p>
        <div className="flex flex-wrap items-end gap-3">
          <span className="display-number text-muted">{pct(result.baseline_p)}</span>
          <Icon name="back" className="mb-2 size-6 rotate-180 text-muted" />
          <span className="display-number brand-text">{pct(result.with_plan_p)}</span>
          <Badge tone={up > 0 ? "good" : "bad"}>
            {up >= 0 ? "+" : ""}
            {Math.round(up * 100)} puntos con el plan
          </Badge>
        </div>
        {up <= 0 && <p className="text-sm text-bad">El plan no mejora tus probabilidades: revísalo antes de invertir esfuerzo.</p>}
        <p className="text-sm">{result.plan.summary}</p>
        <ol className="space-y-2 text-sm">
          {result.plan.steps.map((s: any) => (
            <li key={s.order} className="flex gap-3">
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
                {s.order}
              </span>
              <span>
                {s.action} <span className="text-muted">· {s.when}</span>
              </span>
            </li>
          ))}
        </ol>
        <Link to={`/objetivos/${result.goal_id}`} className="inline-flex min-h-11 items-center gap-1 text-sm font-medium text-accent">
          Seguir este objetivo
          <Icon name="back" className="size-4 rotate-180" />
        </Link>
      </div>
    );
  }

  if (result?.kind === "cycle") {
    const v = result.verdict ?? {};
    const info = verdictInfo(v.decision);
    return (
      <div className="result-glow -mx-4 -mt-4 space-y-3 rounded-t-2xl px-4 pt-5 pb-1 sm:-mx-5 sm:-mt-5 sm:px-5">
        <div className="flex flex-wrap items-center gap-2">
          <VerdictBadge decision={v.decision} />
          <span className="text-sm text-muted">confianza {confidenceWords(v.confidence)} ({pct(v.confidence)})</span>
        </div>
        <p className="text-xl leading-snug font-semibold">
          {result.recommendation?.action?.description ?? "El panel no llegó a una recomendación."}
        </p>
        {info.meaning && <p className="text-sm text-muted">{info.meaning}</p>}
        {v.rationale && <p className="text-sm">{v.rationale}</p>}
        {v.review_triggers?.length > 0 && (
          <div className="rounded-xl bg-surface-2 p-3">
            <p className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">Vuelve a pensarlo si…</p>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {v.review_triggers.map((t: string) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  const forecast = result?.forecast ?? {};
  const outcomes = forecast.outcomes ?? {};
  const top = topOutcome(outcomes);
  const interval: [number, number] | null = Array.isArray(forecast.interval) ? forecast.interval : null;
  const when = resolvesOn(forecast);
  const assumptions = rankAssumptions(forecast.assumptions ?? []);
  const shown = assumptions.slice(0, 3);
  const folded = assumptions.slice(3);
  return (
    <div className="result-glow -mx-4 -mt-4 space-y-4 rounded-t-2xl px-4 pt-5 pb-1 sm:-mx-5 sm:-mt-5 sm:px-5">
      {top && (
        <div>
          <p className="text-sm text-muted">Lo más probable</p>
          <p className="mt-1 text-xl leading-snug font-semibold">{top[0]}</p>
          <div className="mt-2 flex flex-wrap items-baseline gap-3">
            <span className="display-number brand-text">{pct(top[1])}</span>
            <span className={cx("text-sm", top[1] >= 0.55 ? "text-fg" : "text-muted")}>{plainProbability(top[1])}</span>
          </div>
        </div>
      )}
      {!top && (forecast.point != null || interval) && (
        <div>
          <p className="text-sm text-muted">Estimación</p>
          {forecast.point != null && <span className="display-number brand-text">{num(forecast.point)}</span>}
          {interval && (
            <p className="mt-1 text-sm">
              Entre <b>{num(interval[0])}</b> y <b>{num(interval[1])}</b>
              <span className="text-muted"> con un {Math.round((forecast.interval_level ?? 0.9) * 100)}% de probabilidad</span>
            </p>
          )}
        </div>
      )}
      {top && <ProbBars outcomes={outcomes} />}
      {when && (
        <p className="flex items-center gap-1.5 text-sm text-muted">
          <Icon name="calendar" className="size-4" />
          Se sabrá hacia el {when}.
        </p>
      )}
      {shown.length > 0 && (
        <div className="text-sm">
          <p className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">En qué se basa</p>
          <ul className="list-disc space-y-1 pl-5">
            {shown.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
          {folded.length > 0 && (
            <details className="mt-1">
              <summary className="cursor-pointer text-muted">Ver {folded.length} supuesto(s) más</summary>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-muted">
                {folded.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      <p className="text-xs text-muted">
        Guardada en{" "}
        <Link to="/predicciones" className="text-accent underline">
          Predicciones
        </Link>
        : cuando sepas qué pasó, márcalo ahí y Oraqlo aprenderá.
      </p>
    </div>
  );
}
