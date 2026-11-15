import type { Outcomes } from "../lib/api";

/** Distribución de probabilidad como barras ordenadas (la más probable arriba). */
export function ProbBars({ outcomes, highlight }: { outcomes: Outcomes; highlight?: string }) {
  const entries = Object.entries(outcomes).sort((a, b) => b[1] - a[1]);
  return (
    <ul className="space-y-2" aria-label="Distribución de probabilidad">
      {entries.map(([label, p]) => (
        <li key={label}>
          <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
            <span className={label === highlight ? "font-semibold text-good" : ""}>{label}</span>
            <span className="tabular-nums text-muted">{(p * 100).toFixed(0)}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-surface-2">
            <div
              className="h-full rounded-full bg-gradient-to-r from-accent to-accent-2"
              style={{ width: `${Math.max(p * 100, 1.5)}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
