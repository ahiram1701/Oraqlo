import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../components/icons";
import { Badge, Button, Card, Empty, ErrorNote, SectionTitle, Skeleton, cx, inputClass, useFeedback } from "../components/ui";
import { api, fmtDate, pct, type Forecast, type ResolvedForecast } from "../lib/api";

const SOURCE: Record<string, string> = {
  llm: "Modelo de IA",
  quant: "Estadístico",
  ensemble: "Combinado",
  practica: "Práctica",
  practice: "Práctica",
  polymarket: "Práctica",
};

function sourceLabel(source: string) {
  return SOURCE[source.split(/[:/]/)[0]] ?? source;
}

/** El Brier en palabras: el color solo no basta. */
function brierWords(b: number) {
  return b < 0.1 ? "acierto muy bueno" : b < 0.25 ? "acierto bueno" : b < 0.5 ? "acierto flojo" : "fallo";
}

function sortedOutcomes(outcomes: Record<string, number>) {
  return Object.entries(outcomes).sort((a, b) => b[1] - a[1]);
}

export default function Ledger() {
  const { data, error, isLoading } = useQuery({
    queryKey: ["ledger"],
    queryFn: () => api.get<{ open: Forecast[]; resolved: ResolvedForecast[]; overdue?: number }>("/ledger"),
  });
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const open = useMemo(() => (data?.open ?? []).filter((f) => !q || f.question.toLowerCase().includes(q)), [data, q]);
  const resolved = useMemo(() => (data?.resolved ?? []).filter((r) => !q || r.question.toLowerCase().includes(q)), [data, q]);
  const total = (data?.open.length ?? 0) + (data?.resolved.length ?? 0);

  return (
    <div className="space-y-4">
      <ErrorNote error={error} />
      {total > 4 && (
        <label className="relative block">
          <span className="sr-only">Buscar predicciones</span>
          <Icon name="search" className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
          <input className={cx(inputClass, "pl-9")} placeholder="Buscar…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
      )}
      <Card>
        <SectionTitle aside={<Badge>{data?.open.length ?? 0}</Badge>}>Pendientes</SectionTitle>
        <p className="-mt-1 mb-3 text-xs text-muted">
          {data?.overdue
            ? `${data.overdue} ya deberían saberse (salen primero). Toca una y marca qué pasó: así Oraqlo aprende.`
            : "Toca una cuando sepas qué pasó."}
        </p>
        {isLoading && <Skeleton className="h-16" />}
        {data && data.open.length === 0 && (
          <Empty>
            No hay predicciones pendientes. Cada previsión que hagas en{" "}
            <Link to="/" className="text-accent underline">
              Preguntar
            </Link>{" "}
            queda aquí hasta que sepas qué pasó.
          </Empty>
        )}
        <div className="space-y-3">
          {open.map((f) => (
            <OpenForecast key={f.id} f={f} />
          ))}
        </div>
      </Card>
      <Card>
        <SectionTitle aside={<Badge>{data?.resolved.length ?? 0}</Badge>}>Resueltas</SectionTitle>
        {total > 0 && <ExportLink className="-mt-1 mb-3" />}
        {data && data.resolved.length === 0 && (
          <Empty>
            Cuando marques cómo acabó una predicción, aparecerá aquí con su puntuación (Brier: 0 es perfecto; menos es mejor). Así
            Oraqlo aprende.
          </Empty>
        )}
        <ul className="divide-y divide-line">
          {resolved.map((r) => (
            <ResolvedRow key={r.id} r={r} />
          ))}
        </ul>
      </Card>
    </div>
  );
}

export function OpenForecast({ f }: { f: Forecast }) {
  const qc = useQueryClient();
  const { confirm, toast } = useFeedback();
  const [open, setOpen] = useState(false);
  const [other, setOther] = useState("");
  const [pendingOutcome, setPendingOutcome] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["ledger"] });
    qc.invalidateQueries({ queryKey: ["calibration"] });
  };
  const resolve = useMutation({
    mutationFn: (outcome: string) => api.post(`/ledger/${f.id}/resolve`, { outcome }),
    onSuccess: refresh,
    onError: () => setPendingOutcome(null),
  });
  const discard = useMutation({ mutationFn: () => api.del(`/ledger/open/${f.id}`), onSuccess: refresh });
  useEffect(() => () => clearTimeout(timer.current), []);

  /** Resolver afecta a la calibración: 5 s para deshacer antes de enviarlo. */
  function choose(outcome: string) {
    setPendingOutcome(outcome);
    timer.current = setTimeout(() => resolve.mutate(outcome), 5000);
    toast(`Marcada: «${outcome}»`, {
      undo: () => {
        clearTimeout(timer.current);
        setPendingOutcome(null);
      },
    });
  }

  const outcomes = sortedOutcomes(f.outcomes);
  const top = outcomes[0];
  const due = f.created_at ? new Date(new Date(f.created_at).getTime() + f.horizon_days * 864e5) : null;
  const overdue = due ? due < new Date() : false;

  if (pendingOutcome)
    return (
      <div className="rounded-xl border border-dashed border-line p-3 text-sm text-muted" aria-live="polite">
        «{f.question}» → <b className="text-fg">{pendingOutcome}</b>
      </div>
    );

  return (
    <div className="rounded-xl border border-line bg-surface-2">
      <button type="button" aria-expanded={open} className="w-full p-3 text-left" onClick={() => setOpen(!open)}>
        <p className="text-sm font-medium">{f.question}</p>
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          <span>{top ? `Previó: ${top[0]} (${pct(top[1])})` : "Intervalo numérico"}</span>
          <span aria-hidden>·</span>
          <span>{sourceLabel(f.source)}</span>
          <span aria-hidden>·</span>
          <span>se sabrá el {due?.toLocaleDateString("es", { day: "numeric", month: "short" }) ?? "—"}</span>
          {overdue && <Badge tone="warn">ya debería saberse</Badge>}
        </p>
      </button>
      {open && (
        <div className="space-y-3 border-t border-line p-3">
          <p className="text-sm font-medium">¿Qué pasó?</p>
          <div className="flex flex-wrap gap-2">
            {outcomes.map(([label, p]) => (
              <Button key={label} variant="secondary" disabled={resolve.isPending} onClick={() => choose(label)}>
                {label} <span className="text-muted">{pct(p)}</span>
              </Button>
            ))}
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (other.trim()) choose(other.trim());
            }}
          >
            <input
              className={inputClass}
              placeholder="Otro resultado…"
              aria-label="Otro resultado"
              value={other}
              onChange={(e) => setOther(e.target.value)}
            />
            <Button type="submit" variant="secondary" disabled={!other.trim()}>
              Marcar
            </Button>
          </form>
          <Button
            variant="ghost"
            className="px-0 text-xs"
            onClick={async () => {
              if (
                await confirm({
                  title: "¿Descartar sin puntuar?",
                  body: "Se quita de la lista y no cuenta para la calibración.",
                  confirm: "Descartar",
                  danger: true,
                })
              )
                discard.mutate();
            }}
          >
            Descartar sin puntuar
          </Button>
          <ErrorNote error={resolve.error ?? discard.error} />
        </div>
      )}
    </div>
  );
}

function ResolvedRow({ r }: { r: ResolvedForecast }) {
  const qc = useQueryClient();
  const { confirm } = useFeedback();
  const [open, setOpen] = useState(false);
  const del = useMutation({
    mutationFn: () => api.del(`/ledger/resolved/${r.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ledger"] });
      qc.invalidateQueries({ queryKey: ["calibration"] });
    },
  });
  // Clases literales: Tailwind no detecta nombres construidos dinámicamente.
  const toneClass = r.brier < 0.1 ? "text-good" : r.brier < 0.25 ? "text-accent" : r.brier < 0.5 ? "text-warn" : "text-bad";
  return (
    <li className="py-2">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="flex w-full items-start justify-between gap-3 py-1 text-left">
        <span className="min-w-0">
          <span className={cx("block text-sm", !open && "line-clamp-2")}>{r.question}</span>
          <span className="text-xs text-muted">
            Pasó: <b className="text-fg">{r.actual_outcome}</b> · le daba {pct(r.outcomes[r.actual_outcome] ?? 0)}
          </span>
        </span>
        <span className="flex shrink-0 flex-col items-end">
          <span className={cx("text-sm font-semibold tabular-nums", toneClass)}>{r.brier.toFixed(2)}</span>
          <span className="text-xs text-muted">{brierWords(r.brier)}</span>
        </span>
      </button>
      {open && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
          <span>
            Resuelta el {fmtDate(r.resolved_at)} · {sourceLabel(r.source)}
          </span>
          <Button
            variant="ghost"
            className="text-xs hover:text-bad"
            onClick={async () => {
              if (
                await confirm({
                  title: "¿Borrar esta predicción resuelta?",
                  body: "Desaparece del historial de calibración y la fiabilidad se recalcula sin ella.",
                  confirm: "Borrar",
                  danger: true,
                })
              )
                del.mutate();
            }}
          >
            Borrar
          </Button>
        </div>
      )}
    </li>
  );
}

/** Todas las predicciones como CSV, para una hoja de cálculo. */
export function ExportLink({ className }: { className?: string }) {
  return (
    <a href="/api/ledger.csv" download className={cx("inline-flex items-center gap-1.5 text-sm text-accent underline", className)}>
      <Icon name="download" className="size-4" /> Exportar todo (CSV)
    </a>
  );
}
