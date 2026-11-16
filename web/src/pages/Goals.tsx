import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Icon } from "../components/icons";
import { JobTimeline } from "../components/JobView";
import { Button, Card, Empty, ErrorNote, SectionTitle, Skeleton, StatusBadge, cx, inputClass, useFeedback } from "../components/ui";
import { api, pct, type Goal, type Milestone } from "../lib/api";
import { useJob, useStoredJob } from "../lib/useJob";

const shortDate = (iso: string) => {
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("es", { day: "numeric", month: "short" });
};
const longDate = (iso: string) => {
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("es", { day: "numeric", month: "long", year: "numeric" });
};

export function GoalsList() {
  const { data, error, isLoading } = useQuery({ queryKey: ["goals"], queryFn: () => api.get<Goal[]>("/goals") });
  return (
    <div className="space-y-3">
      <ErrorNote error={error} />
      {isLoading && (
        <>
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
        </>
      )}
      {data && data.length === 0 && (
        <Empty>
          Aún no hay objetivos. En{" "}
          <Link to="/" className="text-accent underline">
            Preguntar
          </Link>{" "}
          elige «Lograr objetivo» y Oraqlo te arma un plan con hitos.
        </Empty>
      )}
      {data?.map((g) => (
        <Link key={g.id} to={`/objetivos/${g.id}`} className="block rounded-2xl">
          <Card className="transition hover:border-accent">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium">{g.goal}</p>
                <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <StatusBadge status={g.status} />
                  {g.status === "activo" && <span>{g.days_left} días restantes</span>}
                  {g.overdue.length > 0 && <span className="text-warn">{g.overdue.length} hito(s) vencido(s)</span>}
                </p>
              </div>
              <div className="shrink-0 text-right">
                <p className="text-2xl font-semibold tabular-nums">{pct(g.current_p)}</p>
                <p className="text-xs text-muted">prob. de lograrlo</p>
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

const MILESTONE_OPTIONS: { status: Milestone["status"]; label: string; icon: "check" | "x" | "info"; active: string }[] = [
  { status: "cumplido", label: "Cumplido", icon: "check", active: "bg-good/15 text-good border-good/40" },
  { status: "pendiente", label: "Pendiente", icon: "info", active: "bg-surface text-fg border-line" },
  { status: "fallado", label: "Fallado", icon: "x", active: "bg-bad/15 text-bad border-bad/40" },
];

function MilestoneRow({ goalId, m, i, overdue, active }: { goalId: string; m: Milestone; i: number; overdue: boolean; active: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(m.text);
  const [date, setDate] = useState(m.target_date);
  const save = useMutation({
    mutationFn: (body: Partial<Milestone>) => api.patch<Goal>(`/goals/${goalId}/milestones/${i}`, body),
    onSuccess: (goal) => {
      qc.setQueryData(["goals", goalId], goal);
      qc.invalidateQueries({ queryKey: ["goals"] });
      setEditing(false);
    },
  });

  return (
    <li className="rounded-xl bg-surface-2 p-3">
      {editing ? (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate({ text, target_date: date });
          }}
        >
          <input className={inputClass} value={text} maxLength={300} onChange={(e) => setText(e.target.value)} aria-label="Hito" />
          <div className="flex flex-wrap gap-2">
            <input type="date" className={cx(inputClass, "w-auto")} value={date} onChange={(e) => setDate(e.target.value)} aria-label="Fecha del hito" />
            <Button type="submit" disabled={!text.trim() || save.isPending}>
              Guardar
            </Button>
            <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
              Cancelar
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-medium">{m.text}</p>
            <p className="text-xs text-muted">
              Para el {shortDate(m.target_date)}
              {m.signal && <> · se nota en: {m.signal}</>}
              {overdue && <span className="text-warn"> · vencido</span>}
            </p>
          </div>
          {active && (
            <Button variant="ghost" className="-mt-2 -mr-2 shrink-0 px-2 text-xs" onClick={() => setEditing(true)} aria-label={`Editar el hito ${m.text}`}>
              Editar
            </Button>
          )}
        </div>
      )}
      {active && !editing && (
        <div className="mt-2 grid grid-cols-3 gap-1 rounded-xl" role="radiogroup" aria-label={`Estado de ${m.text}`}>
          {MILESTONE_OPTIONS.map((o) => (
            <button
              key={o.status}
              type="button"
              role="radio"
              aria-checked={m.status === o.status}
              disabled={save.isPending}
              onClick={() => m.status !== o.status && save.mutate({ status: o.status })}
              className={cx(
                "flex min-h-11 items-center justify-center gap-1 rounded-lg border text-xs font-medium transition",
                m.status === o.status ? o.active : "border-transparent text-muted hover:text-fg",
              )}
            >
              <Icon name={o.icon} className="size-4" />
              {o.label}
            </button>
          ))}
        </div>
      )}
      {!active && <div className="mt-2"><StatusBadge status={m.status} /></div>}
      <ErrorNote error={save.error} />
    </li>
  );
}

function GoalHeader({ g }: { g: Goal }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(g.goal);
  const [deadline, setDeadline] = useState(g.deadline);
  const save = useMutation({
    mutationFn: () => api.patch<Goal>(`/goals/${g.id}`, { goal: text, deadline }),
    onSuccess: (goal) => {
      qc.setQueryData(["goals", g.id], goal);
      qc.invalidateQueries({ queryKey: ["goals"] });
      setEditing(false);
    },
  });
  const chart = g.trajectory.map((p, i) => ({
    i,
    label: i === 0 ? "inicio" : shortDate(g.checkins[i - 1]?.date ?? ""),
    p: Math.round(p * 100),
  }));

  return (
    <Card className="overflow-hidden">
      {editing ? (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <textarea className={cx(inputClass, "min-h-16")} value={text} maxLength={500} onChange={(e) => setText(e.target.value)} aria-label="Objetivo" />
          <label className="flex flex-wrap items-center gap-2 text-sm text-muted">
            Fecha límite
            <input type="date" className={cx(inputClass, "w-auto")} value={deadline} onChange={(e) => setDeadline(e.target.value)} />
          </label>
          <div className="flex gap-2">
            <Button type="submit" disabled={!text.trim() || save.isPending}>
              Guardar
            </Button>
            <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
              Cancelar
            </Button>
          </div>
          <ErrorNote error={save.error} />
        </form>
      ) : (
        <>
          <div className="flex items-start justify-between gap-3">
            <h2 className="text-lg leading-snug font-semibold">{g.goal}</h2>
            {g.status === "activo" ? (
              <Button variant="ghost" className="-mt-2 -mr-2 shrink-0 px-2 text-xs" onClick={() => setEditing(true)}>
                Editar
              </Button>
            ) : (
              <StatusBadge status={g.status} />
            )}
          </div>
          <p className="mt-1 text-sm text-muted">
            Para el {longDate(g.deadline)}
            {g.status === "activo" && <> · quedan {g.days_left} días</>}
          </p>
        </>
      )}

      <div className="mt-4 flex flex-wrap items-end gap-x-6 gap-y-2">
        <div>
          <p className="text-xs text-muted">Probabilidad de lograrlo hoy</p>
          <p className="display-number brand-text">{pct(g.current_p)}</p>
        </div>
        <p className="pb-1 text-sm text-muted">sin plan: {pct(g.baseline_p)}</p>
      </div>
      {chart.length > 1 && (
        <div className="mt-3 h-40">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chart} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis dataKey="label" stroke="var(--muted)" fontSize={11} />
              <YAxis domain={[0, 100]} stroke="var(--muted)" fontSize={11} unit="%" />
              <Tooltip
                contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 12 }}
                formatter={(v) => [`${v}%`, "Prob. de lograrlo"]}
              />
              <Line type="monotone" dataKey="p" stroke="var(--accent)" strokeWidth={2.5} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className="mt-3 text-sm">{g.summary}</p>
    </Card>
  );
}

export function GoalDetail() {
  const { id } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { confirm } = useFeedback();
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
  if (!g)
    return (
      <div className="space-y-4" role="status" aria-label="cargando">
        <Skeleton className="h-64" />
        <Skeleton className="h-40" />
      </div>
    );
  const active = g.status === "activo";
  const busy = job.status === "running" || job.status === "connecting";

  return (
    <div className="space-y-4">
      <GoalHeader key={`${g.goal}|${g.deadline}`} g={g} />

      {active && (
        <Card>
          <SectionTitle>¿Cómo vas?</SectionTitle>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (note.trim()) checkin.mutate();
            }}
          >
            <textarea
              className={cx(inputClass, "min-h-20")}
              placeholder="Cuéntalo con tus palabras. Ej.: semana 3 — 3 entrenamientos, 28 km, rodilla bien"
              aria-label="Tu avance"
              value={note}
              maxLength={2000}
              onChange={(e) => setNote(e.target.value)}
            />
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={!note.trim() || busy}>
                Registrar avance
              </Button>
              <Button type="button" variant="secondary" disabled={busy} onClick={() => replan.mutate()}>
                Rehacer el plan
              </Button>
            </div>
          </form>
          <ErrorNote error={checkin.error ?? replan.error} />
          {jobId && (
            <div className="mt-4 space-y-3">
              {job.status === "done" && job.result?.checkin && (
                <div className="rounded-xl bg-surface-2 p-3 text-sm">
                  <p>
                    Nueva estimación: <b className="text-base">{pct(job.result.checkin.p_estimate)}</b>
                  </p>
                  <p className="mt-1 text-muted">{job.result.checkin.comment}</p>
                  {job.result.checkin.replan_recommended && <p className="mt-1 text-warn">Conviene rehacer el plan.</p>}
                </div>
              )}
              <JobTimeline job={job} />
            </div>
          )}
        </Card>
      )}

      <Card>
        <SectionTitle>Hitos</SectionTitle>
        {g.milestones.length === 0 && <Empty>Sin hitos.</Empty>}
        <ul className="space-y-2">
          {g.milestones.map((m, i) => (
            <MilestoneRow key={`${i}|${m.text}|${m.target_date}`} goalId={g.id} m={m} i={i} overdue={g.overdue.includes(i)} active={active} />
          ))}
        </ul>
      </Card>

      <Card>
        <SectionTitle aside={g.plan_version > 1 ? <span className="text-xs text-muted">versión {g.plan_version}</span> : null}>
          Plan
        </SectionTitle>
        <ol className="space-y-3 text-sm">
          {g.steps.map((s) => (
            <li key={s.order} className="flex gap-3">
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
                {s.order}
              </span>
              <span>
                {s.action} <span className="text-muted">· {s.when}</span>
                {s.why && <span className="block text-xs text-muted">{s.why}</span>}
              </span>
            </li>
          ))}
        </ol>
      </Card>

      {g.checkins.length > 0 && (
        <Card>
          <SectionTitle>Avances anteriores</SectionTitle>
          <ul className="space-y-3 text-sm">
            {[...g.checkins].reverse().map((c) => (
              <li key={c.date}>
                <p>
                  <b>{pct(c.p_estimate)}</b> <span className="text-muted">· {shortDate(c.date)}</span>
                </p>
                <p className="text-muted">«{c.note}» — {c.comment}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <SectionTitle>Terminar</SectionTitle>
        <div className="flex flex-wrap gap-2">
          {active && (
            <>
              <Button
                variant="secondary"
                onClick={async () => {
                  if (await confirm({ title: "¿Lo lograste?", body: "Se cierra el objetivo y sus predicciones cuentan como acertadas o falladas.", confirm: "Sí, logrado" }))
                    close.mutate(true);
                }}
              >
                <Icon name="check" className="size-4 text-good" /> Lo logré
              </Button>
              <Button
                variant="secondary"
                onClick={async () => {
                  if (await confirm({ title: "¿No se logró?", body: "Se cierra el objetivo y sus predicciones se resuelven como no logrado.", confirm: "Cerrar" }))
                    close.mutate(false);
                }}
              >
                No se logró
              </Button>
            </>
          )}
          <Button
            variant="danger"
            onClick={async () => {
              if (
                await confirm({
                  title: "¿Eliminar el objetivo?",
                  body: "Se borra con su plan e hitos; sus predicciones pendientes se descartan.",
                  confirm: "Eliminar",
                  danger: true,
                })
              )
                remove.mutate();
            }}
          >
            Eliminar
          </Button>
        </div>
        <ErrorNote error={close.error ?? remove.error} />
      </Card>
    </div>
  );
}
