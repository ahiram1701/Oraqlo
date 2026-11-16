import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ProbBars } from "../components/ProbBars";
import { Badge, Card, Empty, ErrorNote, SectionTitle } from "../components/ui";
import { api, fmtDate, type RunSummary } from "../lib/api";

const KIND = { forecast: "Previsión", cycle: "Análisis", goal: "Objetivo" } as const;

export function HistoryList() {
  const { data, error } = useQuery({ queryKey: ["runs"], queryFn: () => api.get<RunSummary[]>("/runs") });
  return (
    <Card>
      <SectionTitle>Historial de análisis</SectionTitle>
      <ErrorNote error={error} />
      {data && data.length === 0 && (
        <Empty>
          Todavía no hay análisis. Lanza uno en <Link to="/" className="text-accent underline">Preguntar</Link>: su informe completo quedará aquí.
        </Empty>
      )}
      <ul className="divide-y divide-line">
        {data?.map((r) => (
          <li key={r.id}>
            <Link to={`/historial/${r.id}`} className="flex items-center justify-between gap-3 py-3">
              <span className="min-w-0">
                <span className="block truncate text-sm">{r.title}</span>
                <span className="text-xs text-muted">{fmtDate(r.created_at)}</span>
              </span>
              <Badge tone="accent">{KIND[r.kind] ?? r.kind}</Badge>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function RunDetail() {
  const { id } = useParams();
  const { data, error } = useQuery({
    queryKey: ["runs", id],
    queryFn: () => api.get<{ id: string; title: string; kind: string; created_at: string; data: any }>(`/runs/${id}`),
  });
  if (error) return <ErrorNote error={error} />;
  if (!data) return null;
  const d = data.data;
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-start justify-between gap-3">
          <h1 className="text-lg font-semibold">{data.title}</h1>
          <a className="shrink-0 text-sm text-accent underline" href={`/api/runs/${data.id}/report.md`}>
            Descargar .md
          </a>
        </div>
        <p className="text-xs text-muted">{fmtDate(data.created_at)}</p>
      </Card>
      {d.forecast && (
        <Card>
          <SectionTitle>Previsión</SectionTitle>
          <ProbBars outcomes={d.forecast.outcomes} />
        </Card>
      )}
      {d.verdict && (
        <Card>
          <SectionTitle>Veredicto del panel</SectionTitle>
          <p className="text-sm">
            <Badge tone={d.verdict.decision === "emitir" ? "good" : d.verdict.decision === "revisar" ? "warn" : "bad"}>{d.verdict.decision}</Badge>{" "}
            {d.verdict.rationale}
          </p>
          {d.verdict.transcript?.length > 0 && (
            <details className="mt-3">
              <summary className="cursor-pointer text-sm text-accent">Ver el debate completo</summary>
              <div className="mt-2 space-y-2">
                {d.verdict.transcript.map(([role, text]: [string, string], i: number) => (
                  <div key={i} className="rounded-xl bg-surface-2 p-3 text-sm">
                    <p className="mb-1 font-semibold">{role}</p>
                    <p className="whitespace-pre-wrap text-fg/90">{text}</p>
                  </div>
                ))}
              </div>
            </details>
          )}
        </Card>
      )}
      <Card>
        <SectionTitle>Informe</SectionTitle>
        <div className="prose-report text-sm text-fg/90">
          <Markdown remarkPlugins={[remarkGfm]}>{d.markdown}</Markdown>
        </div>
      </Card>
    </div>
  );
}
