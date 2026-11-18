import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, fmtDate, type RunSummary } from "../lib/api";
import { Icon } from "./icons";
import { Card, SectionTitle } from "./ui";

const KIND: Record<string, string> = { forecast: "Previsión", cycle: "Análisis", goal: "Objetivo" };

/** Lo último que preguntaste, bajo el formulario de Preguntar (el historial completo, a un toque). */
export function RecentRuns({ limit = 5 }: { limit?: number }) {
  const { data } = useQuery({
    queryKey: ["runs", { limit }],
    queryFn: () => api.get<RunSummary[]>(`/runs?limit=${limit}`),
  });
  if (!data?.length) return null;
  return (
    <Card>
      <SectionTitle
        aside={
          <Link to="/historial" className="inline-flex min-h-11 items-center text-sm text-accent">
            Ver todo
          </Link>
        }
      >
        Recientes
      </SectionTitle>
      <ul className="-mt-1 divide-y divide-line">
        {[...data].sort((a, b) => b.created_at.localeCompare(a.created_at)).map((r) => (
          <li key={r.id}>
            <Link to={`/historial/${r.id}`} className="flex min-h-12 items-center gap-3 py-2">
              <span className="min-w-0 flex-1">
                <span className="line-clamp-2 block text-sm">{r.title}</span>
                <span className="text-xs text-muted">
                  {KIND[r.kind] ?? r.kind} · {fmtDate(r.created_at)}
                </span>
              </span>
              <Icon name="chevron" className="size-4 shrink-0 -rotate-90 text-muted" />
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}
