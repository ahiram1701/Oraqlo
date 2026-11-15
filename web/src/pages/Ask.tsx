import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { JobTimeline } from "../components/JobView";
import { Badge, Button, Card, ErrorNote, Toggle, cx, inputClass } from "../components/ui";
import { ask, pct, type AskMode } from "../lib/api";
import { useJob, useStoredJob } from "../lib/useJob";

const MODES: { id: AskMode; title: string; hint: string }[] = [
  { id: "forecast", title: "Previsión", hint: "Escenarios con probabilidad · 1 llamada" },
  { id: "full", title: "Análisis completo", hint: "Acciones, simulación y debate del panel" },
  { id: "goal", title: "Lograr objetivo", hint: "Plan, hitos y uplift medible" },
];

const HORIZONS = [7, 30, 90, 180, 365];

export default function Ask() {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<AskMode>("forecast");
  const [horizon, setHorizon] = useState(90);
  const [research, setResearch] = useState(true);
  const [jobId, setJobId] = useStoredJob("ask");
  const job = useJob(jobId);

  const submit = useMutation({
    mutationFn: () => ask(question.trim(), mode, horizon, research),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const busy = submit.isPending || job.status === "running" || job.status === "connecting";

  return (
    <div className="space-y-4">
      <Card>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (question.trim().length >= 3) submit.mutate();
          }}
          className="space-y-4"
        >
          <textarea
            className={cx(inputClass, "min-h-28 resize-y")}
            placeholder={
              mode === "goal"
                ? "¿Qué quieres conseguir? Ej.: correr un maratón en menos de 4 horas"
                : "Escribe tu pregunta. Ej.: ¿Bajará el precio del alquiler en mi ciudad este año?"
            }
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) e.currentTarget.form?.requestSubmit();
            }}
          />
          <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Modo">
            {MODES.map((m) => (
              <button
                type="button"
                key={m.id}
                role="radio"
                aria-checked={mode === m.id}
                onClick={() => setMode(m.id)}
                className={cx(
                  "rounded-xl border p-2.5 text-left transition",
                  mode === m.id ? "border-accent bg-accent/10" : "border-line bg-surface-2",
                )}
              >
                <span className="block text-sm font-medium">{m.title}</span>
                <span className="hidden text-xs text-muted sm:block">{m.hint}</span>
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <label className="flex items-center gap-2 text-sm text-muted">
              Horizonte
              <select className={cx(inputClass, "w-auto py-2")} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
                {HORIZONS.map((d) => (
                  <option key={d} value={d}>
                    {d} días
                  </option>
                ))}
              </select>
            </label>
            <Toggle checked={research} onChange={setResearch} label="Investigar en internet" />
          </div>
          <Button type="submit" className="w-full sm:w-auto" disabled={busy || question.trim().length < 3}>
            {busy ? "Trabajando…" : "Preguntar a Oraqlo"}
          </Button>
          <ErrorNote error={submit.error} />
        </form>
      </Card>

      {jobId && (
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">Resultado</h2>
            <Button variant="ghost" className="min-h-0 px-0" onClick={() => setJobId(null)}>
              Limpiar
            </Button>
          </div>
          <JobTimeline job={job} />
          {job.status === "done" && job.result && <ResultSummary result={job.result} />}
        </Card>
      )}
    </div>
  );
}

function ResultSummary({ result }: { result: any }) {
  if (result.kind === "goal") {
    const up = result.uplift as number;
    return (
      <div className="mt-4 space-y-3 border-t border-line pt-4">
        <p className="text-lg">
          P(objetivo): <b>{pct(result.baseline_p)}</b> → <b>{pct(result.with_plan_p)}</b>{" "}
          <Badge tone={up > 0 ? "good" : "bad"}>
            {up >= 0 ? "+" : ""}
            {pct(up)}
          </Badge>
        </p>
        {up <= 0 && <p className="text-sm text-bad">El plan no aumenta la probabilidad estimada: revísalo antes de invertir esfuerzo.</p>}
        <p className="text-sm">{result.plan.summary}</p>
        <ol className="list-decimal space-y-1 pl-5 text-sm">
          {result.plan.steps.map((s: any) => (
            <li key={s.order}>
              {s.action} <span className="text-muted">— {s.when}</span>
            </li>
          ))}
        </ol>
        <Link to={`/objetivos/${result.goal_id}`} className="inline-block text-sm text-accent underline">
          Abrir seguimiento del objetivo
        </Link>
      </div>
    );
  }
  if (result.kind === "cycle") {
    const v = result.verdict;
    return (
      <div className="mt-4 space-y-2 border-t border-line pt-4">
        <p className="text-sm text-muted">Recomendación</p>
        <p className="text-lg font-semibold">{result.recommendation.action.description}</p>
        <p className="text-sm">
          <Badge tone={v.decision === "emitir" ? "good" : v.decision === "revisar" ? "warn" : "bad"}>{v.decision}</Badge>{" "}
          confianza {pct(v.confidence)} · {v.rationale}
        </p>
        {v.review_triggers?.length > 0 && (
          <ul className="list-disc pl-5 text-sm text-muted">
            {v.review_triggers.map((t: string) => (
              <li key={t}>Revisar si: {t}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  return (
    <div className="mt-4 space-y-3 border-t border-line pt-4">
      {/* La distribución ya se ve en el progreso del job: aquí, los supuestos. */}
      <p className="text-sm font-medium">Supuestos</p>
      {result.forecast.assumptions.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
          {result.forecast.assumptions.map((a: string) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted">
        Registrada en el ledger · resuélvela cuando sepas el resultado para que Oraqlo aprenda.
      </p>
    </div>
  );
}
