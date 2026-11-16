import { useMutation } from "@tanstack/react-query";
import { useRef, useState, type KeyboardEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { Icon } from "../components/icons";
import { JobTimeline } from "../components/JobView";
import { ResultCard, resultText } from "../components/ResultCard";
import { Button, Card, ErrorNote, Notice, Toggle, cx, inputClass, useFeedback } from "../components/ui";
import { ask, type AskMode } from "../lib/api";
import { useSetup } from "../lib/setup";
import { useJob, useStoredJob } from "../lib/useJob";

const MODES: { id: AskMode; title: string; hint: string }[] = [
  { id: "forecast", title: "Previsión", hint: "Qué puede pasar, con la probabilidad de cada escenario. Rápido." },
  {
    id: "full",
    title: "Análisis completo",
    hint: "Propone jugadas, simula futuros y un panel las debate hasta recomendar una. Tarda unos minutos.",
  },
  { id: "goal", title: "Lograr objetivo", hint: "Un plan con hitos y cuánto sube tu probabilidad de lograrlo. Luego lo sigues en Objetivos." },
];

const HORIZONS = [7, 30, 90, 180, 365];
const LAST_KEY = "oraqlo.ask.last";

interface LastAsk {
  question: string;
  mode: AskMode;
  horizon: number;
  research: boolean;
}

function loadLast(): LastAsk | null {
  try {
    return JSON.parse(localStorage.getItem(LAST_KEY) ?? "null");
  } catch {
    return null;
  }
}

function isoInDays(days: number) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function daysUntil(iso: string) {
  const ms = new Date(`${iso}T23:59:59`).getTime() - Date.now();
  return Math.min(3650, Math.max(1, Math.ceil(ms / 86_400_000)));
}

export default function Ask() {
  // "Repetir pregunta" desde el Historial llega con la pregunta y el modo.
  const incoming = (useLocation().state ?? {}) as { question?: string; mode?: AskMode };
  const [question, setQuestion] = useState(incoming.question ?? "");
  const [mode, setMode] = useState<AskMode>(incoming.mode ?? "forecast");
  const [horizon, setHorizon] = useState(90);
  const [deadline, setDeadline] = useState(isoInDays(90));
  const [research, setResearch] = useState(true);
  const [jobId, setJobId] = useStoredJob("ask");
  const [last, setLast] = useState<LastAsk | null>(loadLast);
  const job = useJob(jobId, () => setJobId(null));
  const textarea = useRef<HTMLTextAreaElement>(null);
  const { toast } = useFeedback();
  const setup = useSetup();

  const submit = useMutation({
    mutationFn: (params: LastAsk) => ask(params.question, params.mode, params.horizon, params.research),
    onSuccess: (r, params) => {
      setJobId(r.job_id);
      setLast(params);
      try {
        localStorage.setItem(LAST_KEY, JSON.stringify(params));
      } catch {
        /* sin almacenamiento */
      }
    },
  });
  const busy = submit.isPending || job.status === "running" || job.status === "connecting";
  const days = mode === "goal" ? daysUntil(deadline) : horizon;

  function send(params?: LastAsk) {
    const p = params ?? { question: question.trim(), mode, horizon: days, research };
    if (p.question.length >= 3) submit.mutate(p);
  }

  function onModeKey(e: KeyboardEvent<HTMLDivElement>) {
    const i = MODES.findIndex((m) => m.id === mode);
    const next = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!next) return;
    e.preventDefault();
    const m = MODES[(i + next + MODES.length) % MODES.length];
    setMode(m.id);
    (e.currentTarget.querySelector(`[data-mode="${m.id}"]`) as HTMLElement | null)?.focus();
  }

  async function share() {
    const text = resultText(last?.question ?? "", job.result);
    try {
      if (navigator.share) await navigator.share({ title: "Oraqlo", text });
      else {
        await navigator.clipboard.writeText(text);
        toast("Resultado copiado");
      }
    } catch {
      /* el usuario canceló */
    }
  }

  const done = job.status === "done" && job.result;

  return (
    <div className="space-y-4">
      {setup.data && !setup.data.ready && (
        <Notice tone="warn">
          Aún no hay un modelo de IA listo.{" "}
          <Link to="/bienvenida" className="font-medium underline">
            Configúralo en un minuto
          </Link>
          .
        </Notice>
      )}

      {jobId && (
        <Card className="overflow-hidden">
          {done && <ResultCard result={job.result} />}
          {!done && last?.question && <p className="mb-3 text-sm text-muted">«{last.question}»</p>}
          <div className={cx(done && "mt-4")}>
            <JobTimeline job={job} kind={last?.mode} />
          </div>
          {!busy && (
            <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
              <Button
                variant="secondary"
                onClick={() => {
                  setJobId(null);
                  setQuestion("");
                  textarea.current?.focus();
                }}
              >
                Preguntar otra cosa
              </Button>
              {last && (
                <Button variant="ghost" onClick={() => send(last)} title="Lanzar de nuevo la misma pregunta">
                  <Icon name="repeat" className="size-4" /> Repetir
                </Button>
              )}
              {done && (
                <Button variant="ghost" onClick={share}>
                  <Icon name="share" className="size-4" /> Compartir
                </Button>
              )}
            </div>
          )}
        </Card>
      )}

      <Card>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
          className="space-y-4"
        >
          <label htmlFor="question" className="sr-only">
            Tu pregunta
          </label>
          <textarea
            id="question"
            ref={textarea}
            className={cx(inputClass, "min-h-28 resize-y")}
            placeholder={
              mode === "goal"
                ? "¿Qué quieres conseguir? Ej.: correr un maratón en menos de 4 horas"
                : "Escribe tu pregunta. Ej.: ¿Bajará el precio del alquiler en mi ciudad este año?"
            }
            value={question}
            maxLength={2000}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) e.currentTarget.form?.requestSubmit();
            }}
          />
          {question.length > 1800 && <p className="-mt-2 text-right text-xs text-muted">{question.length} / 2000</p>}
          <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Qué quieres" onKeyDown={onModeKey}>
            {MODES.map((m) => (
              <button
                type="button"
                key={m.id}
                data-mode={m.id}
                role="radio"
                aria-checked={mode === m.id}
                tabIndex={mode === m.id ? 0 : -1}
                onClick={() => setMode(m.id)}
                className={cx(
                  "min-h-14 rounded-xl border p-2.5 text-left text-sm font-medium transition",
                  mode === m.id ? "border-accent bg-accent/10 text-fg" : "border-line bg-surface-2 text-muted hover:text-fg",
                )}
              >
                {m.title}
              </button>
            ))}
          </div>
          <p className="-mt-2 text-xs text-muted">{MODES.find((m) => m.id === mode)?.hint}</p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {mode === "goal" ? (
              <label className="flex items-center gap-2 text-sm whitespace-nowrap text-muted">
                Para el
                <input
                  type="date"
                  className={cx(inputClass, "w-auto py-2")}
                  value={deadline}
                  min={isoInDays(1)}
                  max={isoInDays(3650)}
                  onChange={(e) => setDeadline(e.target.value || isoInDays(90))}
                />
              </label>
            ) : (
              <label className="flex items-center gap-2 text-sm whitespace-nowrap text-muted">
                Pensando a
                <select className={cx(inputClass, "w-auto py-2")} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
                  {HORIZONS.map((d) => (
                    <option key={d} value={d}>
                      {d === 7 ? "1 semana" : d === 30 ? "1 mes" : d === 90 ? "3 meses" : d === 180 ? "6 meses" : "1 año"}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <Toggle checked={research} onChange={setResearch} label="Investigar en internet" />
          </div>
          <Button type="submit" className="w-full sm:w-auto" disabled={busy || question.trim().length < 3}>
            {busy ? "Trabajando…" : "Preguntar a Oraqlo"}
          </Button>
          <ErrorNote error={submit.error} />
        </form>
      </Card>
    </div>
  );
}
