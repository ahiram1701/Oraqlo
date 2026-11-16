import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { JobTimeline } from "../components/JobView";
import { ResultCard } from "../components/ResultCard";
import { Badge, Button, Card, ErrorNote, SectionTitle, Toggle, cx, inputClass, useFeedback } from "../components/ui";
import { api } from "../lib/api";
import { validateCase, type CaseData } from "../lib/caseValidation";
import { useJob, useStoredJob } from "../lib/useJob";

const DRAFT_KEY = "oraqlo.case.draft";

/** "Unexpected token } in JSON at position 120" → "JSON inválido en la línea 7". */
export function jsonError(text: string, error: Error): string {
  const pos = Number(/position (\d+)/.exec(error.message)?.[1]);
  const line = /line (\d+)/.exec(error.message)?.[1];
  const n = line ? Number(line) : Number.isFinite(pos) ? text.slice(0, pos).split("\n").length : null;
  return n ? `JSON inválido en la línea ${n}: revisa comas, comillas y llaves.` : "JSON inválido: revisa comas, comillas y llaves.";
}

function loadDraft() {
  try {
    return localStorage.getItem(DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
}

export default function CaseEditor() {
  const template = useQuery({ queryKey: ["case-template"], queryFn: () => api.get<CaseData>("/cases/template") });
  const [text, setText] = useState(loadDraft);
  const [research, setResearch] = useState(false);
  const [jobId, setJobId] = useStoredJob("case");
  const job = useJob(jobId, () => setJobId(null));
  const { confirm } = useFeedback();
  const seeded = useRef(text !== "");

  // La plantilla se pone UNA vez: borrar todo para empezar de cero ya no la vuelve a meter.
  useEffect(() => {
    if (template.data && !seeded.current) {
      seeded.current = true;
      setText(JSON.stringify(template.data, null, 2));
    }
  }, [template.data]);

  useEffect(() => {
    try {
      localStorage.setItem(DRAFT_KEY, text);
    } catch {
      /* sin almacenamiento */
    }
  }, [text]);

  const parsed = useMemo(() => {
    try {
      return { data: JSON.parse(text) as CaseData, error: null };
    } catch (e) {
      return { data: null, error: text.trim() ? jsonError(text, e as Error) : "Escribe el caso en JSON." };
    }
  }, [text]);
  const issues = parsed.data ? validateCase(parsed.data) : [];

  const run = useMutation({
    mutationFn: () => api.post<{ job_id: string }>("/cases/run", { case: parsed.data, research }),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const done = job.status === "done" && job.result;

  return (
    <div className="space-y-4">
      {jobId && (
        <Card className="overflow-hidden">
          {done && <ResultCard result={job.result} />}
          <div className={cx(done && "mt-4")}>
            <JobTimeline job={job} kind="case" />
          </div>
        </Card>
      )}
      <Card>
        <SectionTitle
          aside={
            parsed.error ? (
              <Badge tone="bad">con errores</Badge>
            ) : issues.length ? (
              <Badge tone="warn">{issues.length} aviso(s)</Badge>
            ) : (
              <Badge tone="good">listo</Badge>
            )
          }
        >
          Caso a mano
        </SectionTitle>
        <p className="mb-3 text-sm text-muted">
          Para expertos: define tú las jugadas, sus resultados posibles con su probabilidad y lo que vale cada resultado. Lo
          normal es usar «Análisis completo» en Preguntar, que redacta este caso por ti.
        </p>
        <textarea
          className={cx(inputClass, "min-h-80 font-mono sm:text-xs")}
          spellCheck={false}
          aria-label="Caso en JSON"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        {parsed.error && <p className="mt-2 text-sm text-bad">{parsed.error}</p>}
        {issues.length > 0 && (
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-warn">
            {issues.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button disabled={!parsed.data || issues.length > 0 || run.isPending} onClick={() => run.mutate()}>
            Analizar este caso
          </Button>
          <Toggle checked={research} onChange={setResearch} label="Investigar en internet" />
          <Button
            variant="ghost"
            onClick={async () => {
              if (
                template.data &&
                (await confirm({ title: "¿Volver a la plantilla?", body: "Se pierde lo que has escrito en el caso.", confirm: "Restaurar", danger: true }))
              )
                setText(JSON.stringify(template.data, null, 2));
            }}
          >
            Restaurar plantilla
          </Button>
        </div>
        <ErrorNote error={run.error} />
      </Card>
    </div>
  );
}
