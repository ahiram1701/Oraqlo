import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { HorizonPicker, daysUntil, fromDays, horizonDays, isoInDays, type Horizon } from "../components/HorizonPicker";
import { Icon } from "../components/icons";
import { JobTimeline } from "../components/JobView";
import { ResultCard, resultText } from "../components/ResultCard";
import { WEB_SEARCH_ANCHOR, useWebSearch } from "../components/WebSearchCard";
import { Button, Card, ErrorNote, Notice, Toggle, cx, inputClass, useFeedback } from "../components/ui";
import { ask, type AskMode } from "../lib/api";
import { useSetup } from "../lib/setup";
import { useJob, useStoredJob } from "../lib/useJob";

const MODES: { id: AskMode; title: string; hint: string }[] = [
  { id: "forecast", title: "Previsión", hint: "Si algo pasará, cuándo o cuánto, con su probabilidad. Tarda segundos." },
  {
    id: "full",
    title: "Análisis completo",
    hint: "Para decidir qué hacer: compara opciones y un panel de expertos recomienda una. Tarda unos minutos.",
  },
  { id: "goal", title: "Lograr objetivo", hint: "Un plan con hitos y cuánto sube tu probabilidad de lograrlo. Luego lo sigues en Objetivos." },
];

/** ¿Solo quiere saber si o cuándo pasará algo? Para eso basta una Previsión. */
export function looksInformational(question: string): boolean {
  const q = question
    .trim()
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "") // sin tildes
    .replace(/^[¿\s]+/, "");
  if (/\b(debo|deberia|conviene|que hago|que hacer|me conviene|como puedo|como hago|vale la pena)\b/.test(q)) return false;
  return /^(cuando|habra|sera|va a|van a|es posible|hay posibilidad|que probabilidad|cuantas? |cuanto |bajara|subira|ganara|llegara|pasara|ocurrira)/.test(q);
}

const LAST_KEY = "oraqlo.ask.last";
const HORIZON_KEY = "oraqlo.ask.horizon";

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

/** El último plazo elegido: el usuario suele pensar siempre a un horizonte parecido. */
function loadHorizon(): Horizon {
  try {
    const saved = JSON.parse(localStorage.getItem(HORIZON_KEY) ?? "null");
    if (saved && (saved.preset === null || typeof saved.preset === "number")) return saved as Horizon;
  } catch {
    /* sin almacenamiento */
  }
  return fromDays(90);
}

export default function Ask() {
  // "Repetir pregunta" desde el Historial llega con la pregunta, el modo y el plazo.
  const incoming = (useLocation().state ?? {}) as { question?: string; mode?: AskMode; horizon?: number };
  const [question, setQuestion] = useState(incoming.question ?? "");
  const [mode, setMode] = useState<AskMode>(incoming.mode ?? "forecast");
  const [horizon, setHorizon] = useState<Horizon>(() => (incoming.horizon ? fromDays(incoming.horizon) : loadHorizon()));
  useEffect(() => {
    try {
      localStorage.setItem(HORIZON_KEY, JSON.stringify(horizon));
    } catch {
      /* sin almacenamiento */
    }
  }, [horizon]);
  // Un objetivo repetido desde el Historial conserva su plazo.
  const [deadline, setDeadline] = useState(() => isoInDays(incoming.mode === "goal" && incoming.horizon ? incoming.horizon : 90));
  const [research, setResearch] = useState(true);
  const { webSearch } = useWebSearch();
  const noSearchKey = research && webSearch?.key_present === false;
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
  const busy =
    submit.isPending || job.status === "running" || job.status === "connecting" || job.status === "queued";
  const days = mode === "goal" ? daysUntil(deadline) : horizonDays(horizon);

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
            <JobTimeline job={job} kind={last?.mode} jobId={jobId} research={last?.research} />
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
          {mode === "full" && looksInformational(question) && (
            <Notice>
              ¿Solo quieres saber si o cuándo pasará? <b>Previsión</b> responde eso en segundos; el análisis completo sirve
              para decidir qué hacer.{" "}
              <button type="button" className="font-medium underline" onClick={() => setMode("forecast")}>
                Usar Previsión
              </button>
            </Notice>
          )}
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
              <HorizonPicker value={horizon} onChange={setHorizon} />
            )}
            <Toggle checked={research} onChange={setResearch} label="Investigar en internet" />
          </div>
          {noSearchKey && (
            <Notice tone="warn">
              Para investigar en internet falta la clave de búsqueda web.{" "}
              <Link to={`/ajustes#${WEB_SEARCH_ANCHOR}`} className="underline">
                Añádela en Ajustes
              </Link>{" "}
              (es gratis). Sin ella, Oraqlo responde solo con lo que ya sabe.
            </Notice>
          )}
          <Button type="submit" className="w-full sm:w-auto" disabled={busy || question.trim().length < 3}>
            {busy ? "Trabajando…" : "Preguntar a Oraqlo"}
          </Button>
          <ErrorNote error={submit.error} />
        </form>
      </Card>
    </div>
  );
}
