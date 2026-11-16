import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { JobTimeline } from "../components/JobView";
import { Badge, Button, Card, ErrorNote, SectionTitle, Toggle, cx, inputClass } from "../components/ui";
import { api, fmtDate, type PracticeState } from "../lib/api";
import { useJob, useStoredJob } from "../lib/useJob";

const STATUS_LABELS: Record<string, string> = {
  queued: "en cola",
  running: "en curso",
  done: "lista",
  failed: "falló",
  cancelled: "cancelada",
};

export default function Practice() {
  const qc = useQueryClient();
  const { data: s, error } = useQuery({
    queryKey: ["practice"],
    queryFn: () => api.get<PracticeState>("/practice"),
    refetchInterval: 30_000,
  });
  const [jobId, setJobId] = useStoredJob("practice");
  const job = useJob(jobId, () => setJobId(null));
  const save = useMutation({
    mutationFn: (body: Partial<Pick<PracticeState, "auto" | "interval_min">>) => api.put<PracticeState>("/practice", body),
    onSuccess: (data) => qc.setQueryData(["practice"], data),
  });
  const run = useMutation({
    mutationFn: () => api.post<{ job_id: string }>("/practice/run"),
    onSuccess: (r) => setJobId(r.job_id),
  });

  const o = s?.reliability_oraqlo;
  const m = s?.reliability_market;
  const verdict = o == null || m == null ? null : o < m ? "Oraqlo supera a la multitud" : "La multitud va por delante";

  return (
    <div className="space-y-4">
      <ErrorNote error={error} />
      <Card>
        <SectionTitle>Oraqlo vs. mercado (Brier, menor es mejor)</SectionTitle>
        <div className="grid grid-cols-2 gap-3">
          <Stat label="Oraqlo" value={o} better={o != null && m != null && o < m} />
          <Stat label="Polymarket" value={m} better={o != null && m != null && m <= o} />
        </div>
        {verdict && <p className="mt-3 text-sm text-muted">{verdict}.</p>}
        {s?.advice && <p className="mt-3 rounded-xl bg-accent/10 p-3 text-sm text-accent">Consejo de calibración: {s.advice}</p>}
      </Card>

      <Card>
        <SectionTitle>Práctica autónoma</SectionTitle>
        <p className="mb-3 text-sm text-muted">
          Solo lectura: cero cuentas, cero apuestas. Oraqlo predice mercados que cierran pronto sin ver el precio y
          compara su Brier con el de la multitud. Corre en el servidor aunque cierres la app.
        </p>
        {s && (
          <div className="space-y-3">
            <Toggle checked={s.auto} onChange={(auto) => save.mutate({ auto })} label="Autónoma" />
            <label className="flex items-center gap-2 text-sm text-muted">
              Cada
              <select
                className={cx(inputClass, "w-auto py-2")}
                value={s.interval_min}
                onChange={(e) => save.mutate({ interval_min: Number(e.target.value) })}
              >
                {[30, 60, 120, 240, 720, 1440].map((n) => (
                  <option key={n} value={n}>
                    {n < 60 ? `${n} min` : `${n / 60} h`}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-xs text-muted">
              Última pasada: {fmtDate(s.last_run)} {s.next_run && <>· próxima: {fmtDate(s.next_run)}</>}
            </p>
            <Button variant="secondary" disabled={s.running || run.isPending} onClick={() => run.mutate()}>
              {s.running ? "Pasada en curso…" : "Ejecutar una pasada ahora"}
            </Button>
          </div>
        )}
        <ErrorNote error={save.error ?? run.error} />
        {jobId && (
          <div className="mt-4">
            <JobTimeline job={job} />
          </div>
        )}
      </Card>

      {s && s.recent.length > 0 && (
        <Card>
          <SectionTitle>Pasadas recientes</SectionTitle>
          <ul className="divide-y divide-line text-sm">
            {s.recent.map((j) => (
              <li key={j.id} className="py-2">
                <div className="flex justify-between gap-2">
                  <span>{fmtDate(j.created_at)}</span>
                  <Badge tone={j.status === "done" ? "good" : j.status === "failed" ? "bad" : "accent"}>
                    {STATUS_LABELS[j.status] ?? j.status}
                  </Badge>
                </div>
                {j.error && <p className="mt-1 text-xs text-bad">{j.error}</p>}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function Stat({ label, value, better }: { label: string; value: number | null | undefined; better: boolean }) {
  return (
    <div className={cx("rounded-xl border p-3", better ? "border-good/40 bg-good/5" : "border-line bg-surface-2")}>
      <p className="text-xs text-muted">{label}</p>
      <p className="text-2xl font-semibold tabular-nums">{value == null ? "—" : value.toFixed(3)}</p>
    </div>
  );
}
