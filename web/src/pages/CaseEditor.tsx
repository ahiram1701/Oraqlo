import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { JobTimeline } from "../components/JobView";
import { Badge, Button, Card, ErrorNote, SectionTitle, Toggle, cx, inputClass } from "../components/ui";
import { api } from "../lib/api";
import { validateCase, type CaseData } from "../lib/caseValidation";
import { useJob, useStoredJob } from "../lib/useJob";

export default function CaseEditor() {
  const template = useQuery({ queryKey: ["case-template"], queryFn: () => api.get<CaseData>("/cases/template") });
  const [text, setText] = useState("");
  const [research, setResearch] = useState(false);
  const [jobId, setJobId] = useStoredJob("case");
  const job = useJob(jobId, () => setJobId(null));

  useEffect(() => {
    if (template.data && !text) setText(JSON.stringify(template.data, null, 2));
  }, [template.data, text]);

  const parsed = useMemo(() => {
    try {
      return { data: JSON.parse(text) as CaseData, error: null };
    } catch (e) {
      return { data: null, error: (e as Error).message };
    }
  }, [text]);
  const issues = parsed.data ? validateCase(parsed.data) : [];

  const run = useMutation({
    mutationFn: () => api.post<{ job_id: string }>("/cases/run", { case: parsed.data, research }),
    onSuccess: (r) => setJobId(r.job_id),
  });

  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle aside={parsed.error ? <Badge tone="bad">JSON inválido</Badge> : issues.length ? <Badge tone="warn">{issues.length} aviso(s)</Badge> : <Badge tone="good">válido</Badge>}>
          Caso estratégico
        </SectionTitle>
        <p className="mb-3 text-sm text-muted">
          Define acciones candidatas, sus resultados posibles con probabilidad y la utilidad de cada resultado. El análisis
          completo desde “Preguntar” redacta este caso por ti.
        </p>
        <textarea
          className={cx(inputClass, "min-h-80 font-mono text-xs")}
          spellCheck={false}
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
            Ejecutar ciclo completo
          </Button>
          <Toggle checked={research} onChange={setResearch} label="Investigar en internet" />
          <Button variant="ghost" onClick={() => template.data && setText(JSON.stringify(template.data, null, 2))}>
            Restaurar plantilla
          </Button>
        </div>
        <ErrorNote error={run.error} />
      </Card>
      {jobId && (
        <Card>
          <SectionTitle>Ejecución</SectionTitle>
          <JobTimeline job={job} />
        </Card>
      )}
    </div>
  );
}
