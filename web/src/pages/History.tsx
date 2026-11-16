import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import Markdown from "react-markdown";
import { Link, useNavigate, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";
import { Icon } from "../components/icons";
import { Turn } from "../components/JobView";
import { ResultCard } from "../components/ResultCard";
import { ChipGroup, FilteredEmpty, SearchInput, SortSelect, countLabel, includesText, type Option } from "../components/listControls";
import { Badge, Button, Card, Empty, ErrorNote, LinkButton, SectionTitle, Skeleton } from "../components/ui";
import { api, fmtDate, type RunSummary } from "../lib/api";
import { useStoredState } from "../lib/useStoredState";

const KIND: Record<string, string> = { forecast: "Previsión", cycle: "Análisis", goal: "Objetivo" };
const MODE_FOR_KIND: Record<string, string> = { forecast: "forecast", cycle: "full", goal: "goal" };

const PAGE = 100;
type RunKind = "all" | "forecast" | "cycle" | "goal";
type RunSort = "recientes" | "antiguos" | "az";
const RUN_SORTS: Option<RunSort>[] = [
  { value: "recientes", label: "Más recientes" },
  { value: "antiguos", label: "Más antiguos" },
  { value: "az", label: "Pregunta (A-Z)" },
];

export function sortRuns(list: RunSummary[], by: RunSort): RunSummary[] {
  const out = [...list];
  if (by === "recientes") out.sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (by === "antiguos") out.sort((a, b) => a.created_at.localeCompare(b.created_at));
  if (by === "az") out.sort((a, b) => a.title.localeCompare(b.title, "es", { sensitivity: "base" }));
  return out;
}

export function HistoryList() {
  // De 100 en 100: antes solo se veían (y se buscaba en) los últimos 50.
  const [limit, setLimit] = useState(PAGE);
  const { data, error, isLoading, isFetching } = useQuery({
    queryKey: ["runs", { limit }],
    queryFn: () => api.get<RunSummary[]>(`/runs?limit=${limit}`),
    placeholderData: (prev) => prev,
  });
  const [query, setQuery] = useState("");
  const [kind, setKind] = useStoredState<RunKind>("oraqlo.history.kind", "all");
  const [sort, setSort] = useStoredState<RunSort>("oraqlo.history.sort", "recientes");
  const shown = useMemo(
    () => sortRuns((data ?? []).filter((r) => (kind === "all" || r.kind === kind) && includesText(r.title, query)), sort),
    [data, query, kind, sort],
  );
  const more = (data?.length ?? 0) >= limit;

  return (
    <Card>
      <ErrorNote error={error} />
      {data && data.length > 0 && (
        <div className="mb-3 space-y-2">
          <SearchInput value={query} onChange={setQuery} label="Buscar en el historial" />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <ChipGroup
              label="Tipo"
              options={[
                { value: "all", label: "Todo" },
                { value: "forecast", label: "Previsiones" },
                { value: "cycle", label: "Análisis" },
                { value: "goal", label: "Objetivos" },
              ]}
              value={kind}
              onChange={setKind}
            />
            <SortSelect options={RUN_SORTS} value={sort} onChange={setSort} />
          </div>
          {shown.length !== data.length && <p className="text-xs text-muted">{countLabel(shown.length, data.length)}</p>}
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
      {data && data.length > 0 && shown.length === 0 && (
        <FilteredEmpty
          onClear={() => {
            setQuery("");
            setKind("all");
          }}
        />
      )}
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
      {more && (
        <Button variant="secondary" className="mt-3 w-full" disabled={isFetching} onClick={() => setLimit(limit + PAGE)}>
          {isFetching ? "Cargando…" : "Cargar más antiguos"}
        </Button>
      )}
    </Card>
  );
}

export function RunDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data, error, refetch } = useQuery({
    queryKey: ["runs", id],
    queryFn: () => api.get<{ id: string; title: string; kind: string; created_at: string; data: any }>(`/runs/${id}`),
  });
  if (error)
    return (
      <Card className="space-y-3">
        <ErrorNote error={error} onRetry={() => refetch()} />
        <Button variant="secondary" onClick={() => navigate("/historial")}>
          Volver al historial
        </Button>
      </Card>
    );
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
