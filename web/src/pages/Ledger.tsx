import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, SectionTitle, cx, inputClass } from "../components/ui";
import { api, fmtDate, pct, type Forecast, type ResolvedForecast } from "../lib/api";

export default function Ledger() {
  const { data, error } = useQuery({
    queryKey: ["ledger"],
    queryFn: () => api.get<{ open: Forecast[]; resolved: ResolvedForecast[] }>("/ledger"),
  });
  return (
    <div className="space-y-4">
      <ErrorNote error={error} />
      <Card>
        <SectionTitle aside={<Badge>{data?.open.length ?? 0}</Badge>}>Abiertas</SectionTitle>
        {data && data.open.length === 0 && <Empty>No hay predicciones abiertas.</Empty>}
        <div className="space-y-3">
          {data?.open.map((f) => <OpenForecast key={f.id} f={f} />)}
        </div>
      </Card>
      <Card>
        <SectionTitle aside={<Badge>{data?.resolved.length ?? 0}</Badge>}>Resueltas</SectionTitle>
        {data && data.resolved.length === 0 && <Empty>Cuando resuelvas predicciones verás aquí su Brier.</Empty>}
        <ul className="divide-y divide-line">
          {data?.resolved.map((r) => <ResolvedRow key={r.id} r={r} />)}
        </ul>
      </Card>
    </div>
  );
}

function OpenForecast({ f }: { f: Forecast }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [other, setOther] = useState("");
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["ledger"] });
    qc.invalidateQueries({ queryKey: ["calibration"] });
  };
  const resolve = useMutation({
    mutationFn: (outcome: string) => api.post(`/ledger/${f.id}/resolve`, { outcome }),
    onSuccess: refresh,
  });
  const discard = useMutation({ mutationFn: () => api.del(`/ledger/open/${f.id}`), onSuccess: refresh });
  const top = Object.entries(f.outcomes)[0];
  const due = f.created_at ? new Date(new Date(f.created_at).getTime() + f.horizon_days * 864e5) : null;
  const overdue = due ? due < new Date() : false;

  return (
    <div className="rounded-xl border border-line bg-surface-2 p-3">
      <button className="w-full text-left" onClick={() => setOpen(!open)}>
        <p className="text-sm font-medium">{f.question}</p>
        <p className="mt-1 text-xs text-muted">
          {top ? `${top[0]} ${pct(top[1])}` : "intervalo"} · {f.source} · vence {due?.toLocaleDateString("es") ?? "—"}{" "}
          {overdue && <Badge tone="warn">vencida</Badge>}
        </p>
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          <p className="text-xs text-muted">¿Qué pasó?</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(f.outcomes).map(([label, p]) => (
              <Button key={label} variant="secondary" disabled={resolve.isPending} onClick={() => resolve.mutate(label)}>
                {label} <span className="text-muted">{pct(p)}</span>
              </Button>
            ))}
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (other.trim()) resolve.mutate(other.trim());
            }}
          >
            <input className={inputClass} placeholder="Otro resultado…" value={other} onChange={(e) => setOther(e.target.value)} />
            <Button type="submit" variant="secondary" disabled={!other.trim()}>
              Resolver
            </Button>
          </form>
          <Button
            variant="ghost"
            className="min-h-0 px-0 text-xs"
            onClick={() => confirm("¿Descartar sin puntuar? No afecta a la calibración.") && discard.mutate()}
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
  const del = useMutation({
    mutationFn: () => api.del(`/ledger/resolved/${r.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ledger"] });
      qc.invalidateQueries({ queryKey: ["calibration"] });
    },
  });
  // Clases literales: Tailwind no detecta nombres construidos dinámicamente.
  const toneClass = r.brier < 0.1 ? "text-good" : r.brier < 0.3 ? "text-warn" : "text-bad";
  return (
    <li className="flex items-start justify-between gap-3 py-3">
      <div className="min-w-0">
        <p className="truncate text-sm">{r.question}</p>
        <p className="text-xs text-muted">
          pasó: <b className="text-fg">{r.actual_outcome}</b> (predicho {pct(r.outcomes[r.actual_outcome] ?? 0)}) ·{" "}
          {fmtDate(r.resolved_at)}
        </p>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        <span className={cx("text-sm font-semibold tabular-nums", toneClass)}>{r.brier.toFixed(3)}</span>
        <button
          className="text-xs text-muted hover:text-bad"
          onClick={() => confirm("Borra historial de calibración: la fiabilidad se recalcula sin ella. ¿Seguro?") && del.mutate()}
        >
          borrar
        </button>
      </div>
    </li>
  );
}
