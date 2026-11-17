import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ClarifyItem, JobEvent } from "../lib/api";
import { ROLE_LABELS, api, pct } from "../lib/api";
import type { JobState } from "../lib/useJob";
import { Icon } from "./icons";
import { ProbBars } from "./ProbBars";
import { WEB_SEARCH_ANCHOR } from "./WebSearchCard";
import { Button, ErrorNote, Notice, Spinner, VerdictBadge, cx, verdictInfo } from "./ui";

const MarkdownText = lazy(() => import("./MarkdownText"));

/** Los mismos nombres que en Ajustes → Modelos del panel. */
export const ROLE_LABEL: Record<string, string> = ROLE_LABELS;

const ROLE_HINT: Record<string, string> = {
  estratega: "propone la jugada",
  red_team: "busca por qué podría fallar",
  oraculo: "estima probabilidades",
  juez: "decide",
};

const ROLE_TONE: Record<string, string> = {
  estratega: "border-accent/50",
  red_team: "border-bad/50",
  oraculo: "border-accent-2/50",
  juez: "border-warn/50",
};

export const PHASE_LABEL: Record<string, string> = {
  investigar: "Investigando en internet",
  preguntar: "Preguntándote lo que internet no sabe",
  redactar: "Planteando las opciones",
  observar: "Ordenando lo que se sabe",
  prever: "Estimando probabilidades",
  simular: "Simulando futuros",
  planificar: "Comparando riesgos y beneficios",
  deliberar: "Debate del panel",
  linea_base: "Estimando tus probabilidades hoy",
  palancas: "Viendo qué controlas y qué no",
  plan: "Diseñando el plan",
  con_plan: "Estimando con el plan",
};

/** Fases que se esperan según el tipo de trabajo (para mostrar lo que falta). */
export const EXPECTED_PHASES: Record<string, string[]> = {
  forecast: ["prever"],
  full: ["prever", "redactar", "observar", "simular", "planificar", "deliberar"],
  case: ["observar", "prever", "simular", "planificar", "deliberar"],
  goal: ["linea_base", "palancas", "plan", "deliberar", "con_plan"],
};

/** Con investigación activa, «Investigando» aparece como primer paso esperado. */
export function expectedPhases(kind: string | undefined, research = false): string[] {
  const base = EXPECTED_PHASES[kind ?? ""] ?? [];
  return research && ["forecast", "full", "goal"].includes(kind ?? "") ? ["investigar", ...base] : base;
}

export function prettyTurn(role: string, text: string): string {
  try {
    const data = JSON.parse(text);
    if (role === "red_team") return (data.objections ?? []).map((o: string) => `- ${o}`).join("\n");
    if (role === "oraculo") {
      const lines = [`**Probabilidad de éxito: ${pct(data.p_exito)}**`];
      for (const [k, v] of Object.entries(data.p_objeciones ?? {})) lines.push(`- ${k}: ${pct(v as number)}`);
      return lines.join("\n");
    }
    if (role === "juez") {
      const v = verdictInfo(data.decision);
      return [`**${v.label}** · confianza ${pct(data.confidence)}`, data.rationale].filter(Boolean).join("\n\n");
    }
  } catch {
    /* texto libre */
  }
  return text;
}

/** Un turno del debate, plegable. */
export function Turn({ role, text, defaultOpen = false }: { role: string; text: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={cx("rounded-xl border-l-4 bg-surface-2", ROLE_TONE[role] ?? "border-line")}>
      <button
        type="button"
        aria-expanded={open}
        className="flex min-h-11 w-full items-center justify-between gap-2 px-3 text-left text-sm"
        onClick={() => setOpen(!open)}
      >
        <span>
          <span className="font-semibold">{ROLE_LABEL[role] ?? role}</span>
          {ROLE_HINT[role] && <span className="text-muted"> · {ROLE_HINT[role]}</span>}
        </span>
        <Icon name="chevron" className={cx("size-4 text-muted transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="prose-report px-3 pb-3 text-sm text-fg/90">
          <Suspense fallback={<p className="whitespace-pre-wrap">{prettyTurn(role, text)}</p>}>
            <MarkdownText>{prettyTurn(role, text)}</MarkdownText>
          </Suspense>
        </div>
      )}
    </div>
  );
}

function useElapsed(running: boolean) {
  const start = useRef<number | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!running) return;
    start.current ??= Date.now();
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);
  if (start.current === null) return null;
  const s = Math.max(0, Math.round((now - start.current) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Pasos del trabajo: hechos, el actual y los que faltan. */
function Stepper({ events, expected, running }: { events: JobEvent[]; expected: string[]; running: boolean }) {
  const seen: string[] = [];
  for (const e of events) if (e.type === "phase" && !seen.includes(e.data.name)) seen.push(e.data.name);
  const steps = [...seen, ...expected.filter((p) => !seen.includes(p))];
  if (steps.length === 0) return null;
  const current = running ? seen.at(-1) : undefined;
  return (
    <ol className="space-y-1.5">
      {steps.map((name) => {
        const done = seen.includes(name) && name !== current;
        const active = name === current;
        return (
          <li key={name} className={cx("flex items-center gap-2 text-sm", active ? "text-fg" : done ? "text-muted" : "text-muted/60")}>
            <span className="flex size-5 items-center justify-center">
              {active ? (
                <Spinner decorative className="size-3.5 text-accent" />
              ) : done ? (
                <Icon name="check" className="size-4 text-good" />
              ) : (
                <span className="size-1.5 rounded-full bg-current" />
              )}
            </span>
            <span aria-current={active ? "step" : undefined} className={cx(active && "font-medium")}>
              {PHASE_LABEL[name] ?? name}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** Lo que el usuario contestó cuando Oraqlo le preguntó. */
function AnswersList({ items }: { items: ClarifyItem[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Lo que nos contaste</p>
      <ul className="space-y-1.5 text-sm">
        {items.map((q) => (
          <li key={q.id}>
            <span className="text-muted">{q.text}</span>{" "}
            {q.answer ? <span className="font-medium">{q.answer}</span> : <span className="text-muted italic">sin responder</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** El contenido del proceso: hallazgos, debate, veredictos y detalles técnicos. */
function Process({ job, showForecast }: { job: JobState; showForecast: boolean }) {
  const turns = job.events.filter((e) => e.type === "turn");
  const lastTurn = turns.at(-1)?.seq;
  const logs = job.events.filter((e) => e.type === "log");
  const facts = job.events.filter((e) => e.type === "fact");
  const noSearchKey = logs.some((e) => e.data.code === "no_websearch_key");
  return (
    <div className="space-y-3">
      {noSearchKey && (
        <Notice tone="warn">
          No investigó en internet: falta la clave de búsqueda web.{" "}
          <Link to={`/ajustes#${WEB_SEARCH_ANCHOR}`} className="underline">
            Añádela en Ajustes
          </Link>
          .
        </Notice>
      )}
      {facts.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Lo que encontró</p>
          <ul className="space-y-1.5 text-sm">
            {facts.map((ev) => (
              <li key={ev.seq} className="flex gap-2">
                <Icon name="check" className="mt-0.5 size-4 text-good" />
                <span>
                  {ev.data.text}{" "}
                  <a href={ev.data.url} target="_blank" rel="noreferrer" className="text-xs text-accent underline">
                    fuente
                  </a>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {job.events.map((ev) => {
        switch (ev.type) {
          case "answers":
            return <AnswersList key={ev.seq} items={ev.data.items ?? []} />;
          case "forecast":
            return showForecast ? (
              <div key={ev.seq} className="rounded-xl bg-surface-2 p-3">
                <ProbBars outcomes={ev.data.outcomes} />
              </div>
            ) : null;
          case "turn":
            return <Turn key={ev.seq} role={ev.data.role} text={ev.data.text} defaultOpen={ev.seq === lastTurn} />;
          case "verdict":
            return (
              <p key={ev.seq} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-muted">Sobre «{ev.data.candidate}»:</span>
                <VerdictBadge decision={ev.data.decision} />
                <span className="text-muted">confianza {pct(ev.data.confidence)}</span>
              </p>
            );
          case "revision":
            return (
              <p key={ev.seq} className="text-sm text-warn">
                Revisando la jugada con {ev.data.objections.length} objeción(es)…
              </p>
            );
          default:
            return null;
        }
      })}
      {logs.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted">Detalles técnicos ({logs.length})</summary>
          <div className="mt-2 space-y-1">
            {logs.map((ev) => (
              <p key={ev.seq} className={cx("text-xs", ev.data.level === "warn" ? "text-warn" : "text-muted")}>
                {ev.data.msg}
              </p>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

/**
 * Progreso de un job. Mientras corre: pasos con el actual, tiempo y lo que va
 * saliendo. Al terminar, todo eso se pliega en "Cómo llegó a esto" (el resultado
 * lo muestra la pantalla que lanzó el trabajo).
 */
const STATUS_TEXT: Record<string, string> = {
  connecting: "Conectando…",
  queued: "En cola: empieza cuando termine lo anterior…",
  running: "Pensando…",
};

export function JobTimeline({
  job,
  kind,
  jobId,
  research = false,
  onRetried,
}: {
  job: JobState;
  kind?: string;
  /** Si se pasa, aparece el botón «Cancelar». */
  jobId?: string | null;
  research?: boolean;
  /** Con jobId: si falló o se canceló, «Reintentar» lo relanza y avisa del job nuevo. */
  onRetried?: (newJobId: string) => void;
}) {
  const running = job.status === "running" || job.status === "connecting" || job.status === "queued";
  const elapsed = useElapsed(running);
  const expected = expectedPhases(kind, research);
  const [cancelling, setCancelling] = useState<"idle" | "pending" | "asked" | "error">("idle");
  const [retrying, setRetrying] = useState<"idle" | "pending" | "error">("idle");

  async function retry() {
    if (!jobId || !onRetried) return;
    setRetrying("pending");
    try {
      const r = await api.post<{ job_id: string }>(`/jobs/${jobId}/retry`);
      setRetrying("idle");
      setCancelling("idle");
      onRetried(r.job_id);
    } catch {
      setRetrying("error");
    }
  }

  async function cancel() {
    if (!jobId) return;
    setCancelling("pending");
    try {
      await api.post(`/jobs/${jobId}/cancel`);
      setCancelling("asked");
    } catch {
      setCancelling("error");
    }
  }

  if (running) {
    const stopping = cancelling === "pending" || cancelling === "asked";
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-2">
          <p className="flex items-center gap-2 text-sm font-medium text-accent" aria-live="polite">
            <Spinner decorative />
            {stopping ? "Cancelando: termina el paso en curso…" : STATUS_TEXT[job.status] ?? "Pensando…"}
          </p>
          <span className="flex items-center gap-2">
            {elapsed && <span className="text-xs tabular-nums text-muted">{elapsed}</span>}
            {jobId && (
              <Button variant="ghost" className="min-h-9 px-2" disabled={stopping} onClick={cancel}>
                Cancelar
              </Button>
            )}
          </span>
        </div>
        {cancelling === "error" && <Notice tone="bad">No se pudo cancelar. Vuelve a intentarlo.</Notice>}
        <Stepper events={job.events} expected={expected} running />
        <p className="text-xs text-muted">Puedes bloquear el celular o cerrar la app: sigue en el servidor y te avisa al terminar.</p>
        <Process job={job} showForecast />
      </div>
    );
  }

  const hasProcess = job.events.some((e) => e.type !== "phase");
  return (
    <div className="space-y-3">
      {job.status === "failed" && <ErrorNote error={job.error ?? "El trabajo falló"} />}
      {job.status === "cancelled" && <Notice>Cancelado. Lo que alcanzó a hacer queda abajo.</Notice>}
      {(job.status === "failed" || job.status === "cancelled") && jobId && onRetried && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" disabled={retrying === "pending"} onClick={retry}>
            <Icon name="repeat" className="size-4" /> {retrying === "pending" ? "Reintentando…" : "Reintentar"}
          </Button>
          {retrying === "error" && <span className="text-sm text-bad">No se pudo reintentar.</span>}
        </div>
      )}
      {hasProcess && (
        <details className="group rounded-xl border border-line p-3">
          <summary className="flex cursor-pointer list-none items-center justify-between text-sm text-muted">
            Cómo llegó a esto
            <Icon name="chevron" className="size-4 transition group-open:rotate-180" />
          </summary>
          <div className="mt-3 space-y-3">
            <Stepper events={job.events} expected={[]} running={false} />
            <Process job={job} showForecast={job.status !== "done"} />
          </div>
        </details>
      )}
      {job.runId && (
        <Link to={`/historial/${job.runId}`} className="inline-flex min-h-11 items-center text-sm text-accent underline">
          Ver informe completo
        </Link>
      )}
    </div>
  );
}

