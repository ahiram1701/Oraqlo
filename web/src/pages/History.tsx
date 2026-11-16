import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import Markdown from "react-markdown";
import { Link, useNavigate, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";
import { Icon } from "../components/icons";
import { Turn } from "../components/JobView";
import { ResultCard } from "../components/ResultCard";
import { Badge, Button, Card, Empty, ErrorNote, LinkButton, SectionTitle, Skeleton, cx, inputClass } from "../components/ui";
import { api, fmtDate, type RunSummary } from "../lib/api";

const KIND: Record<string, string> = { forecast: "Previsión", cycle: "Análisis", goal: "Objetivo" };
const MODE_FOR_KIND: Record<string, string> = { forecast: "forecast", cycle: "full", goal: "goal" };

export function HistoryList() {
  const { data, error, isLoading } = useQuery({ queryKey: ["runs"], queryFn: () => api.get<RunSummary[]>("/runs") });
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<string>("all");
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data ?? []).filter((r) => (kind === "all" || r.kind === kind) && (!q || r.title.toLowerCase().includes(q)));
  }, [data, query, kind]);

  return (
    <Card>
      <ErrorNote error={error} />
      {data && data.length > 0 && (
        <div className="mb-3 space-y-2">
          <label className="relative block">
            <span className="sr-only">Buscar en el historial</span>
            <Icon name="search" className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
            <input
              className={cx(inputClass, "pl-9")}
              placeholder="Buscar…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <div className="no-scrollbar flex gap-2 overflow-x-auto" role="group" aria-label="Tipo">
            {[["all", "Todo"], ["forecast", "Previsiones"], ["cycle", "Análisis"], ["goal", "Objetivos"]].map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={kind === value}
                onClick={() => setKind(value)}
                className={cx(
                  "min-h-9 shrink-0 rounded-full border px-3 text-sm transition",
                  kind === value ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:text-fg",
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}
      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </div>
      )}
      {data && data.length === 0 && (
        <Empty>
          Todavía no hay análisis. Lanza uno en{" "}
          <Link to="/" className="text-accent underline">
            Preguntar
          </Link>
          : su informe completo quedará aquí.
        </Empty>
      )}
      {data && data.length > 0 && shown.length === 0 && <Empty>Nada coincide con la búsqueda.</Empty>}
      <ul className="divide-y divide-line">
        {shown.map((r) => (
          <li key={r.id}>
            <Link to={`/historial/${r.id}`} className="flex min-h-14 items-center justify-between gap-3 py-3">
              <span className="min-w-0">
                <span className="line-clamp-2 text-sm">{r.title}</span>
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
  const navigate = useNavigate();
  const { data, error } = useQuery({
    queryKey: ["runs", id],
    queryFn: () => api.get<{ id: string; title: string; kind: string; created_at: string; data: any }>(`/runs/${id}`),
  });
  if (error) return <ErrorNote error={error} />;
  if (!data)
    return (
      <div className="space-y-4" role="status" aria-label="cargando">
        <Skeleton className="h-24" />
        <Skeleton className="h-48" />
      </div>
    );
  const d = data.data;
  const transcript: [string, string][] = d.verdict?.transcript ?? [];
  return (
    <div className="space-y-4">
      <Card className="overflow-hidden">
        <h2 className="text-lg leading-snug font-semibold">{data.title}</h2>
        <p className="mb-4 text-xs text-muted">
          {KIND[data.kind] ?? data.kind} · {fmtDate(data.created_at)}
        </p>
        <div className="border-t border-line pt-4">
          <ResultCard result={d} />
        </div>
        <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-3">
          <Button
            variant="secondary"
            onClick={() => navigate("/", { state: { question: data.title, mode: MODE_FOR_KIND[data.kind] } })}
          >
            <Icon name="repeat" className="size-4" /> Repetir pregunta
          </Button>
          <LinkButton variant="ghost" href={`/api/runs/${data.id}/report.md`}>
            Descargar informe
          </LinkButton>
        </div>
      </Card>
      {transcript.length > 0 && (
        <Card>
          <SectionTitle>El debate del panel</SectionTitle>
          <div className="space-y-2">
            {transcript.map(([role, text], i) => (
              <Turn key={i} role={role} text={text} />
            ))}
          </div>
        </Card>
      )}
      {d.markdown && (
        <Card>
          <details>
            <summary className="cursor-pointer text-sm font-semibold tracking-wide text-muted uppercase">Informe completo</summary>
            <div className="prose-report mt-3 text-sm text-fg/90">
              <Markdown remarkPlugins={[remarkGfm]}>{d.markdown}</Markdown>
            </div>
          </details>
        </Card>
      )}
    </div>
  );
}
