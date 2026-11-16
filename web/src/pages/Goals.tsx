import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { JobTimeline } from "../components/JobView";
import { Badge, Button, Card, Empty, ErrorNote, SectionTitle, cx, inputClass } from "../components/ui";
import { api, pct, type Goal, type Milestone } from "../lib/api";
import { useJob, useStoredJob } from "../lib/useJob";

const STATUS_TONE = { activo: "accent", logrado: "good", "no-logrado": "bad", abandonado: "muted" } as const;

export function GoalsList() {
  const { data, error } = useQuery({ queryKey: ["goals"], queryFn: () => api.get<Goal[]>("/goals") });
  return (
    <div className="space-y-3">
      <ErrorNote error={error} />
      {data && data.length === 0 && (
        <Empty>
          Aún no hay objetivos. En <Link to="/" className="text-accent underline">Preguntar</Link> elige “Lograr objetivo”.
        </Empty>
      )}
      {data?.map((g) => (
        <Link key={g.id} to={`/objetivos/${g.id}`} className="block">
          <Card className="transition hover:border-accent">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium">{g.goal}</p>
                <p className="mt-1 text-xs text-muted">
                  {g.status === "activo" ? `${g.days_left} días restantes` : g.status} · plan v{g.plan_version}
                  {g.overdue.length > 0 && (
                    <>
                      {" "}
                      · <span className="text-warn">{g.overdue.length} hito(s) vencido(s)</span>
                    </>
                  )}
                </p>
              </div>
              <div className="text-right">
                <p className="text-2xl font-semibold tabular-nums">{pct(g.current_p)}</p>
                <Badge tone={STATUS_TONE[g.status]}>{g.status}</Badge>
              </div>
            </div>
            <Sparkline values={g.trajectory} />
          </Card>
        </Link>
      ))}
    </div>
  );
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const w = 100;
  const h = 24;
  const points = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - v * h}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="mt-3 h-6 w-full" preserveAspectRatio="none" aria-hidden>
      <polyline points={points} fill="none" stroke="var(--accent)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function GoalDetail() {
  const { id } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data: g, error } = useQuery({ queryKey: ["goals", id], queryFn: () => api.get<Goal>(`/goals/${id}`) });
  const [note, setNote] = useState("");
  const [jobId, setJobId] = useStoredJob(`goal.${id}`);
  const job = useJob(jobId, () => setJobId(null));
  const refresh = () => qc.invalidateQueries({ queryKey: ["goals"] });

  const checkin = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(`/goals/${id}/checkin`, { note }),
    onSuccess: (r) => {
      setJobId(r.job_id);
      setNote("");
    },
  });
  const replan = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(`/goals/${id}/replan`),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const milestone = useMutation({
    mutationFn: ({ i, status }: { i: number; status: Milestone["status"] }) =>
      api.put(`/goals/${id}/milestones/${i}`, { status }),
    onSuccess: refresh,
  });
  const close = useMutation({
    mutationFn: (achieved: boolean) => api.post(`/goals/${id}/close`, { achieved }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/goals/${id}`),
    onSuccess: () => {
      refresh();
      navigate("/objetivos");
    },
  });

  if (error) return <ErrorNote error={error} />;
  if (!g) return null;
  const active = g.status === "activo";
  const busy = job.status === "running" || job.status === "connecting";
  const chart = g.trajectory.map((p, i) => ({
    i,
    label: i === 0 ? "inicio" : (g.checkins[i - 1]?.date ?? "").slice(5, 10),
    p: Math.round(p * 100),
  }));

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-start justify-between gap-3">
          <h1 className="text-lg font-semibold">{g.goal}</h1>
          <Badge tone={STATUS_TONE[g.status]}>{g.status}</Badge>
        </div>
        <p className="mt-1 text-sm text-muted">
          Fecha límite {g.deadline} · {g.days_left} días · statu quo {pct(g.baseline_p)} · plan v{g.plan_version}
        </p>
        <div className="mt-4 h-44">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chart} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis dataKey="label" stroke="var(--muted)" fontSize={11} />
              <YAxis domain={[0, 100]} stroke="var(--muted)" fontSize={11} unit="%" />
              <Tooltip
                contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 12 }}
                formatter={(v) => [`${v}%`, "P(objetivo)"]}
              />
              <Line type="monotone" dataKey="p" stroke="var(--accent)" strokeWidth={2.5} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <p className="mt-2 text-sm">{g.summary}</p>
      </Card>

      {active && (
        <Card>
          <SectionTitle>Check-in</SectionTitle>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (note.trim()) checkin.mutate();
            }}
          >
            <textarea
              className={cx(inputClass, "min-h-20")}
              placeholder="¿Cómo vas? Ej.: semana 3 — 3 entrenamientos, 28 km, rodilla bien"
              value={note}
              maxLength={2000}
              onChange={(e) => setNote(e.target.value)}
            />
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={!note.trim() || busy}>
                Registrar avance
              </Button>
              <Button type="button" variant="secondary" disabled={busy} onClick={() => replan.mutate()}>
                Re-planificar
              </Button>
            </div>
          </form>
          <ErrorNote error={checkin.error ?? replan.error} />
          {jobId && (
            <div className="mt-4">
              <JobTimeline job={job} />
              {job.status === "done" && job.result?.checkin && (
                <p className="mt-2 text-sm">
                  Nueva estimación <b>{pct(job.result.checkin.p_estimate)}</b> — {job.result.checkin.comment}
                  {job.result.checkin.replan_recommended && <span className="text-warn"> · se recomienda re-planificar</span>}
                </p>
              )}
            </div>
          )}
        </Card>
      )}

      <Card>
        <SectionTitle>Hitos</SectionTitle>
        {g.milestones.length === 0 && <Empty>Sin hitos.</Empty>}
        <ul className="space-y-2">
          {g.milestones.map((m, i) => (
            <li key={i} className="rounded-xl bg-surface-2 p-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-medium">{m.text}</p>
                  <p className="text-xs text-muted">
                    {m.target_date} · señal: {m.signal}
                    {g.overdue.includes(i) && <span className="text-warn"> · vencido</span>}
                  </p>
                </div>
                <Badge tone={m.status === "cumplido" ? "good" : m.status === "fallado" ? "bad" : "muted"}>{m.status}</Badge>
              </div>
              {active && (
                <div className="mt-2 flex gap-2">
                  {(["cumplido", "fallado", "pendiente"] as const)
                    .filter((s) => s !== m.status)
                    .map((s) => (
                      <button key={s} className="text-xs text-accent underline" onClick={() => milestone.mutate({ i, status: s })}>
                        marcar {s}
                      </button>
                    ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      </Card>

      <Card>
        <SectionTitle>Plan</SectionTitle>
        <ol className="list-decimal space-y-2 pl-5 text-sm">
          {g.steps.map((s) => (
            <li key={s.order}>
              {s.action} <span className="text-muted">— {s.when}</span>
              {s.why && <p className="text-xs text-muted">{s.why}</p>}
            </li>
          ))}
        </ol>
      </Card>

      {g.checkins.length > 0 && (
        <Card>
          <SectionTitle>Historial de check-ins</SectionTitle>
          <ul className="space-y-3 text-sm">
            {[...g.checkins].reverse().map((c) => (
              <li key={c.date}>
                <p>
                  <b>{pct(c.p_estimate)}</b> <span className="text-muted">· {c.date.slice(0, 10)}</span>
                </p>
                <p className="text-muted">“{c.note}” — {c.comment}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <SectionTitle>Cerrar</SectionTitle>
        <div className="flex flex-wrap gap-2">
          {active && (
            <>
              <Button variant="secondary" onClick={() => confirm("¿Marcar como LOGRADO? Resuelve sus predicciones.") && close.mutate(true)}>
                Logrado
              </Button>
              <Button variant="secondary" onClick={() => confirm("¿Marcar como NO logrado? Resuelve sus predicciones.") && close.mutate(false)}>
                No logrado
              </Button>
            </>
          )}
          <Button variant="danger" onClick={() => confirm("¿Eliminar el objetivo? Sus predicciones abiertas se descartan.") && remove.mutate()}>
            Eliminar
          </Button>
        </div>
        <ErrorNote error={milestone.error ?? close.error ?? remove.error} />
      </Card>
    </div>
  );
}
