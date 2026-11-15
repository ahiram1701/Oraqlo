import { useState } from "react";
import { Link } from "react-router-dom";
import type { JobEvent } from "../lib/api";
import { pct } from "../lib/api";
import type { JobState } from "../lib/useJob";
import { ProbBars } from "./ProbBars";
import { Badge, Card, ErrorNote, Spinner, cx } from "./ui";

const ROLE_LABEL: Record<string, string> = {
  estratega: "Estratega",
  red_team: "Red Team",
  oraculo: "Oráculo",
  juez: "Juez",
};

const ROLE_TONE: Record<string, string> = {
  estratega: "border-accent/40",
  red_team: "border-bad/40",
  oraculo: "border-accent-2/40",
  juez: "border-warn/40",
};

const PHASE_LABEL: Record<string, string> = {
  investigar: "Investigando en internet",
  redactar: "Redactando el caso",
  observar: "Observando",
  prever: "Previsión del oráculo",
  simular: "Simulando futuros",
  planificar: "Planificando (EU + CVaR)",
  deliberar: "Panel deliberando",
};

function prettyTurn(role: string, text: string): string {
  try {
    const data = JSON.parse(text);
    if (role === "red_team") return (data.objections ?? []).map((o: string) => `• ${o}`).join("\n");
    if (role === "oraculo") {
      const lines = [`P(éxito): ${pct(data.p_exito)}`];
      for (const [k, v] of Object.entries(data.p_objeciones ?? {})) lines.push(`• ${k}: ${pct(v as number)}`);
      return lines.join("\n");
    }
    if (role === "juez") {
      return [`${String(data.decision).toUpperCase()} · confianza ${pct(data.confidence)}`, data.rationale]
        .filter(Boolean)
        .join("\n");
    }
  } catch {
    /* texto libre */
  }
  return text;
}

function Turn({ ev }: { ev: JobEvent }) {
  const [open, setOpen] = useState(ev.data.role !== "estratega");
  const text = prettyTurn(ev.data.role, ev.data.text);
  return (
    <div className={cx("rounded-xl border-l-4 bg-surface-2 p-3", ROLE_TONE[ev.data.role] ?? "border-line")}>
      <button className="flex w-full items-center justify-between text-left text-sm font-semibold" onClick={() => setOpen(!open)}>
        {ROLE_LABEL[ev.data.role] ?? ev.data.role}
        <span className="text-xs text-muted">{open ? "ocultar" : "ver"}</span>
      </button>
      {open && <p className="mt-2 text-sm whitespace-pre-wrap text-fg/90">{text}</p>}
    </div>
  );
}

/** Progreso en vivo de un job: fases, hechos, debate rol a rol y veredicto. */
export function JobTimeline({ job }: { job: JobState }) {
  const running = job.status === "running" || job.status === "connecting" || job.status === "queued";
  const lastPhase = [...job.events].reverse().find((e) => e.type === "phase");
  return (
    <div className="space-y-3">
      {running && (
        <div className="flex items-center gap-2 text-sm text-accent">
          <Spinner />
          {lastPhase ? PHASE_LABEL[lastPhase.data.name] ?? lastPhase.data.name : "En cola…"}
          <span className="text-muted">· puedes bloquear el celular, sigue en el servidor</span>
        </div>
      )}
      {job.events.map((ev) => {
        switch (ev.type) {
          case "phase":
            return (
              <p key={ev.seq} className="pt-1 text-xs font-semibold tracking-wide text-muted uppercase">
                {PHASE_LABEL[ev.data.name] ?? ev.data.name}
                {ev.data.candidate ? ` · ${ev.data.candidate}` : ""}
              </p>
            );
          case "log":
            return (
              <p key={ev.seq} className={cx("text-sm", ev.data.level === "warn" ? "text-warn" : "text-muted")}>
                {ev.data.msg}
              </p>
            );
          case "fact":
            return (
              <p key={ev.seq} className="text-sm">
                <span className="text-good">●</span> {ev.data.text}{" "}
                <a href={ev.data.url} target="_blank" rel="noreferrer" className="text-xs text-accent underline">
                  fuente
                </a>
              </p>
            );
          case "forecast":
            return (
              <div key={ev.seq} className="rounded-xl bg-surface-2 p-3">
                <ProbBars outcomes={ev.data.outcomes} />
              </div>
            );
          case "turn":
            return <Turn key={ev.seq} ev={ev} />;
          case "verdict":
            return (
              <p key={ev.seq} className="text-sm">
                Veredicto sobre <b>{ev.data.candidate}</b>:{" "}
                <Badge tone={ev.data.decision === "emitir" ? "good" : ev.data.decision === "revisar" ? "warn" : "bad"}>
                  {ev.data.decision}
                </Badge>{" "}
                <span className="text-muted">confianza {pct(ev.data.confidence)}</span>
              </p>
            );
          case "revision":
            return (
              <p key={ev.seq} className="text-sm text-warn">
                Re-deliberando con {ev.data.objections.length} objeción(es)…
              </p>
            );
          default:
            return null;
        }
      })}
      {job.status === "failed" && <ErrorNote error={job.error ?? "El trabajo falló"} />}
      {job.runId && (
        <Link to={`/historial/${job.runId}`} className="inline-block text-sm text-accent underline">
          Ver informe completo
        </Link>
      )}
    </div>
  );
}

export function JobCard({ title, job }: { title: string; job: JobState }) {
  return (
    <Card>
      <h3 className="mb-3 font-semibold">{title}</h3>
      <JobTimeline job={job} />
    </Card>
  );
}
