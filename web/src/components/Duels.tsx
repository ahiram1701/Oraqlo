// Práctica, pregunta a pregunta: quién se acercó más, Oraqlo o el mercado.
import { useMemo } from "react";
import { fmtDate, pct, type Duel, type DuelSummary } from "../lib/api";
import { useStoredState } from "../lib/useStoredState";
import { ChipGroup, FilteredEmpty, SortSelect, countLabel, type Option } from "./listControls";
import { Badge, Card, Empty, SectionTitle, cx } from "./ui";

type DuelFilter = "todos" | "ganados" | "perdidos" | "abiertos";
type DuelSort = "recientes" | "diferencia" | "cierre";

const SORTS: Option<DuelSort>[] = [
  { value: "recientes", label: "Más recientes" },
  { value: "diferencia", label: "Más diferencia" },
  { value: "cierre", label: "Cierran antes" },
];

/** «De 10 duelos: Oraqlo gana 2 y el mercado 8», o una frase honesta si aún no hay. */
export function duelHeadline(s: DuelSummary | undefined): string {
  if (!s || s.resolved === 0) return "Aún no se ha resuelto ningún duelo.";
  const ties = s.tied ? `; ${s.tied} empate${s.tied > 1 ? "s" : ""}` : "";
  return `De ${s.resolved} duelo${s.resolved > 1 ? "s" : ""} resuelto${s.resolved > 1 ? "s" : ""}: Oraqlo gana ${s.won} y el mercado ${s.lost}${ties}.`;
}

/** Los duelos que no cuentan: el mercado ya sabía el resultado al predecir. */
export function decidedNote(s: DuelSummary | undefined): string | null {
  const n = s?.already_decided ?? 0;
  return n
    ? `${n} no cuenta${n > 1 ? "n" : ""}: cuando Oraqlo predijo, el mercado ya sabía el resultado (p. ej. un partido ya jugado).`
    : null;
}

export function filterDuels(items: Duel[], filter: DuelFilter): Duel[] {
  if (filter === "ganados") return items.filter((d) => d.winner === "oraqlo");
  if (filter === "perdidos") return items.filter((d) => d.winner === "mercado");
  if (filter === "abiertos") return items.filter((d) => d.status === "abierto");
  return items;
}

export function sortDuels(items: Duel[], by: DuelSort): Duel[] {
  const out = [...items];
  if (by === "diferencia") out.sort((a, b) => Math.abs(b.oraqlo_p - b.market_p) - Math.abs(a.oraqlo_p - a.market_p));
  if (by === "cierre") out.sort((a, b) => (a.closes_at ?? "").localeCompare(b.closes_at ?? ""));
  return out; // «recientes»: el servidor ya los manda así
}

function Bar({ label, p, tone }: { label: string; p: number; tone: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-16 shrink-0 text-muted">{label}</span>
      <span className="h-2 flex-1 overflow-hidden rounded-full bg-surface-2" aria-hidden>
        <span className={cx("block h-full rounded-full", tone)} style={{ width: `${Math.round(p * 100)}%` }} />
      </span>
      <span className="w-10 shrink-0 text-right tabular-nums">{pct(p)}</span>
    </div>
  );
}

function DuelRow({ d }: { d: Duel }) {
  const badge =
    d.winner === "oraqlo" ? (
      <Badge tone="good">Ganó Oraqlo</Badge>
    ) : d.winner === "mercado" ? (
      <Badge tone="bad">Ganó el mercado</Badge>
    ) : d.winner === "empate" ? (
      <Badge>Empate</Badge>
    ) : d.already_decided ? (
      <Badge>Ya se sabía</Badge>
    ) : (
      <Badge tone="accent">Abierto</Badge>
    );
  return (
    <li className="space-y-2 py-3">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 text-sm">{d.question}</p>
        <span className="shrink-0">{badge}</span>
      </div>
      <p className="text-xs text-muted">
        {d.status === "resuelto" ? (
          <>
            Pasó: <b className="text-fg">{d.actual}</b>. Probabilidad que le daba cada uno a «{d.label}»:
          </>
        ) : (
          <>
            Probabilidad de «<b className="text-fg">{d.label}</b>»
            {d.closes_at && <> · se sabrá hacia el {fmtDate(d.closes_at)}</>}:
          </>
        )}
      </p>
      <div className="space-y-1">
        <Bar label="Oraqlo" p={d.oraqlo_p} tone="bg-accent" />
        <Bar label="Mercado" p={d.market_p} tone="bg-muted" />
      </div>
    </li>
  );
}

export function DuelsCard({ items }: { items: Duel[] }) {
  const [filter, setFilter] = useStoredState<DuelFilter>("oraqlo.practice.duels", "todos");
  const [sort, setSort] = useStoredState<DuelSort>("oraqlo.practice.duelSort", "recientes");
  const shown = useMemo(() => sortDuels(filterDuels(items, filter), sort), [items, filter, sort]);
  const count = (f: DuelFilter) => filterDuels(items, f).length;
  return (
    <Card>
      <SectionTitle aside={<span className="text-xs text-muted">{countLabel(shown.length, items.length)}</span>}>
        Duelos con el mercado
      </SectionTitle>
      {items.length === 0 ? (
        <Empty>Cuando Oraqlo prediga mercados, aquí verás cada pregunta: lo que dijo él, lo que decía el mercado y quién acertó más.</Empty>
      ) : (
        <>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <ChipGroup
              label="Duelos"
              options={[
                { value: "todos", label: "Todos", count: items.length },
                { value: "ganados", label: "Ganados", count: count("ganados") },
                { value: "perdidos", label: "Perdidos", count: count("perdidos") },
                { value: "abiertos", label: "Abiertos", count: count("abiertos") },
              ]}
              value={filter}
              onChange={setFilter}
            />
            <SortSelect options={SORTS} value={sort} onChange={setSort} />
          </div>
          {shown.length === 0 && <FilteredEmpty onClear={() => setFilter("todos")} />}
          <ul className="divide-y divide-line">
            {shown.map((d) => (
              <DuelRow key={d.market_id} d={d} />
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
