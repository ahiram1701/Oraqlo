import { useState } from "react";
import { answerJob, type QuestionsResult } from "../lib/api";
import { Button, ErrorNote, cx, inputClass } from "./ui";

/**
 * Oraqlo investigó y aun así le falta algo que solo tú sabes: te lo pregunta.
 * Respondes lo que quieras (no hace falta todo) o sigues sin responder, y el
 * análisis continúa en un trabajo nuevo (`onContinue`).
 */
export function ClarifyCard({
  jobId,
  result,
  onContinue,
}: {
  jobId: string;
  result: QuestionsResult;
  onContinue: (newJobId: string) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [sending, setSending] = useState<"answer" | "skip" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const filled = Object.fromEntries(Object.entries(answers).filter(([, v]) => v.trim()));
  const hasAnswers = Object.keys(filled).length > 0;

  async function send(skip: boolean) {
    setSending(skip ? "skip" : "answer");
    setError(null);
    try {
      const r = await answerJob(jobId, skip ? null : filled);
      onContinue(r.job_id);
    } catch (e) {
      setError(e);
      setSending(null);
    }
  }

  const set = (id: string, value: string) => setAnswers((a) => ({ ...a, [id]: value }));

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (hasAnswers) void send(false);
      }}
    >
      <div>
        <h2 className="text-base font-semibold">Me falta algo que solo tú sabes</h2>
        <p className="mt-1 text-sm text-muted">
          Esto no está en internet y cambia bastante la respuesta. Contesta lo que quieras: lo que dejes en blanco lo
          supondré y lo diré.
        </p>
      </div>
      {result.questions.map((q, i) => (
        <div key={q.id} className="space-y-1.5">
          <label htmlFor={`clarify-${q.id}`} className="block text-sm font-medium">
            {i + 1}. {q.text}
          </label>
          {q.why && <p className="text-xs text-muted">{q.why}</p>}
          {q.options.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {q.options.map((option) => (
                <button
                  type="button"
                  key={option}
                  aria-pressed={answers[q.id] === option}
                  onClick={() => set(q.id, option)}
                  className={cx(
                    "min-h-9 rounded-full border px-3 text-sm transition",
                    answers[q.id] === option
                      ? "border-accent bg-accent/10 text-fg"
                      : "border-line bg-surface-2 text-muted hover:text-fg",
                  )}
                >
                  {option}
                </button>
              ))}
            </div>
          )}
          <input
            id={`clarify-${q.id}`}
            className={inputClass}
            placeholder={q.options.length ? "O escríbelo con tus palabras" : "Tu respuesta"}
            maxLength={2000}
            value={answers[q.id] ?? ""}
            onChange={(e) => set(q.id, e.target.value)}
          />
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={!hasAnswers || sending !== null}>
          {sending === "answer" ? "Enviando…" : "Responder y seguir"}
        </Button>
        <Button type="button" variant="ghost" disabled={sending !== null} onClick={() => void send(true)}>
          {sending === "skip" ? "Siguiendo…" : "Continuar sin responder"}
        </Button>
      </div>
      <ErrorNote error={error} />
    </form>
  );
}
