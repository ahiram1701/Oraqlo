import { pct, type WatchView } from "../lib/api";
import { Icon } from "./icons";
import { Badge } from "./ui";

/** Detalle de las voces extra del comité (ver forecaster.markets y forecaster.decompose). */
export interface Voices {
  members?: Record<string, Record<string, number>>;
  market?: {
    platform: string;
    question: string;
    url: string;
    outcomes: Record<string, number>;
    crowd: string;
    why: string;
  };
  structure?: {
    mode: "y" | "o";
    yes_label: string;
    parts: { text: string; p: number }[];
    p_yes: number;
  };
}

export interface Analysis {
  best: string | null;
  best_text?: string;
  summary?: string[];
  voi?: { action: string; action_text: string; value: number }[];
  probability?: {
    action_text: string;
    outcome: string;
    p: number;
    threshold: number;
    direction: "sube" | "baja";
    new_best: string;
  }[];
  approximate?: boolean;
}

const PLATFORM: Record<string, string> = {
  polymarket: "Polymarket",
  manifold: "Manifold",
  metaculus: "Metaculus",
};

function Title({ children }: { children: string }) {
  return <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">{children}</h3>;
}

/** Qué más se tuvo en cuenta: el mercado de predicción y la estimación por partes. */
export function VoicesNote({ voices }: { voices?: Voices | null }) {
  if (!voices?.market && !voices?.structure) return null;
  const m = voices.market;
  const s = voices.structure;
  return (
    <section aria-label="Otras voces" className="space-y-3 rounded-xl bg-surface-2 p-3 text-sm">
      {m && (
        <div className="space-y-1">
          <p className="flex flex-wrap items-center gap-2">
            <Badge tone="good">Mercado</Badge>
            <span>
              En {PLATFORM[m.platform] ?? m.platform}
              {m.crowd ? ` (${m.crowd})` : ""} se apuesta sobre lo mismo:
            </span>
          </p>
          <p className="text-muted">
            {m.url ? (
              <a href={m.url} target="_blank" rel="noreferrer" className="text-accent underline">
                «{m.question}»
              </a>
            ) : (
              `«${m.question}»`
            )}{" "}
            →{" "}
            {Object.entries(m.outcomes)
              .map(([k, v]) => `${k} ${pct(v)}`)
              .join(", ")}
          </p>
          <p className="text-xs text-muted">Cuenta como una voz más del comité, con el peso que se gana acertando.</p>
        </div>
      )}
      {s && (
        <div className="space-y-1">
          <p className="flex flex-wrap items-center gap-2">
            <Badge>Por partes</Badge>
            <span>{s.mode === "y" ? "Tienen que pasar todas:" : "Basta con que pase una:"}</span>
          </p>
          <ol className="list-decimal space-y-0.5 pl-5 text-muted">
            {s.parts.map((p) => (
              <li key={p.text}>
                {p.text} <b className="text-fg">{pct(p.p)}</b>
              </li>
            ))}
          </ol>
          <p className="text-xs text-muted">Juntas dan {pct(s.p_yes)}: otra voz del comité.</p>
        </div>
      )}
    </section>
  );
}

/** Lo que Oraqlo vigilará por su cuenta para avisarte si algo cambia. */
export function WatchNote({ watch }: { watch?: WatchView | null }) {
  if (!watch || !watch.signals.length) return null;
  return (
    <details className="rounded-xl bg-surface-2 p-3 text-sm">
      <summary className="flex cursor-pointer items-center gap-2 text-muted">
        <Icon name="bell" className="size-4" />
        Vigilando {watch.signals.length} señales: te avisaré si algo cambia
      </summary>
      <SignalList watch={watch} />
    </details>
  );
}

export function SignalList({ watch }: { watch: WatchView }) {
  return (
    <ul className="mt-2 list-disc space-y-1 pl-5">
      {watch.signals.map((s) => (
        <li key={s.text}>
          {s.text}
          {s.favors && <span className="text-muted"> → empuja hacia «{s.favors}»</span>}
          {s.triggered_at && (
            <>
              {" "}
              <Badge tone="warn">ya pasó</Badge>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Cómo se movió la probabilidad de lo más probable en cada revisión. */
export function Sparkline({ watch, label }: { watch: WatchView; label: string }) {
  const points = watch.history.map((h) => h.outcomes[label] ?? 0);
  if (points.length < 2) return null;
  const w = 120;
  const h = 32;
  const xy = points.map((p, i) => `${(i / (points.length - 1)) * w},${h - p * h}`).join(" ");
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      width={w}
      height={h}
      role="img"
      aria-label={`Evolución de «${label}»: ${points.map((p) => pct(p)).join(" → ")}`}
    >
      <polyline points={xy} fill="none" stroke="currentColor" strokeWidth="2" className="text-accent" />
    </svg>
  );
}

/** Puntos de ruptura y valor de la información del análisis completo. */
export function BreakPoints({ analysis }: { analysis?: Analysis | null }) {
  if (!analysis?.summary?.length) return null;
  return (
    <section aria-labelledby="ruptura" className="space-y-2 border-t border-line pt-4 text-sm">
      <Title>Qué haría cambiar la recomendación</Title>
      <ul className="list-disc space-y-1 pl-5" id="ruptura">
        {analysis.summary.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {analysis.approximate && (
        <p className="text-xs text-muted">Calculado a un paso: el caso tiene varias decisiones seguidas.</p>
      )}
    </section>
  );
}

export function Premortem({ items }: { items?: { cause: string; signal: string }[] }) {
  if (!items?.length) return null;
  return (
    <section className="space-y-2 border-t border-line pt-4 text-sm">
      <Title>Si sale mal, probablemente será por…</Title>
      <ul className="space-y-2">
        {items.map((p) => (
          <li key={p.cause}>
            <p>{p.cause}</p>
            {p.signal && <p className="text-xs text-muted">Señal temprana: {p.signal}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function RivalNote({
  rival,
  actions,
}: {
  rival?: {
    description: string;
    responses: Record<string, { response: string; p: number }[]>;
  } | null;
  actions?: Record<string, { description: string }>;
}) {
  if (!rival) return null;
  return (
    <section className="space-y-2 border-t border-line pt-4 text-sm">
      <Title>{`Cómo respondería ${rival.description}`}</Title>
      <ul className="space-y-1">
        {Object.entries(rival.responses).map(([aid, list]) => {
          const top = [...list].sort((a, b) => b.p - a.p)[0];
          return (
            <li key={aid}>
              Si «{actions?.[aid]?.description ?? aid}»: <b>{top?.response}</b>{" "}
              <span className="text-muted">({pct(top?.p)})</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export interface LeverView {
  text: string;
  impact: string;
  effort: string;
  uplift?: number | null;
}

/** Las palancas del objetivo, ya ordenadas por puntos de probabilidad por esfuerzo. */
export function LeversRanked({ levers }: { levers?: LeverView[] }) {
  if (!levers?.length) return null;
  return (
    <section className="space-y-2 text-sm">
      <Title>Haz esto primero</Title>
      <ol className="space-y-1">
        {levers.slice(0, 4).map((l, i) => (
          <li key={l.text} className="flex items-baseline gap-2">
            <span className="text-xs text-muted">{i + 1}.</span>
            <span className="min-w-0 flex-1">{l.text}</span>
            {typeof l.uplift === "number" && <Badge tone="good">+{Math.round(l.uplift)} pts</Badge>}
            <span className="text-xs text-muted">esfuerzo {l.effort}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
