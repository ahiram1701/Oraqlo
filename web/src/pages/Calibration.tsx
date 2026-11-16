import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { ChipGroup, SortSelect, type Option } from "../components/listControls";
import { Card, Empty, ErrorNote, SectionTitle, Skeleton, cx, inputClass } from "../components/ui";
import {
  FORECAST_KIND_LABEL,
  api,
  fmtDate,
  pct,
  sourceLabel,
  type Calibration as Cal,
  type ForecastKind,
  type ScoreStats,
} from "../lib/api";
import { useStoredState } from "../lib/useStoredState";
import { ExportLink } from "./Ledger";

type Period = "todo" | "365" | "90";
type SourceSort = "mejor" | "datos" | "nombre";

/** «Acierta un 34% mejor que el azar», en palabras. */
export function skillWords(skill: number | null | undefined): string {
  if (skill == null) return "—";
  if (skill >= 0.05) return `${Math.round(skill * 100)}% mejor que el azar`;
  if (skill <= -0.05) return `${Math.round(-skill * 100)}% peor que el azar`;
  return "igual que el azar";
}

/** La frase que resume la calibración: ¿se pasa de confiado o se queda corto? */
export function calibrationSentence(s: Cal["summary"] | undefined): string {
  if (!s || s.n < 5 || s.confidence == null || s.hit_rate == null) return "Aún hay pocas predicciones resueltas para sacar conclusiones.";
  const said = `Cuando apuesta por algo, de media está un ${pct(s.confidence)} seguro y acierta el ${pct(s.hit_rate)}.`;
  const gap = s.overconfidence ?? 0;
  if (Math.abs(gap) < 0.06) return `${said} Bien calibrado.`;
  return gap > 0 ? `${said} Se pasa de confiado.` : `${said} Es más prudente de lo necesario.`;
}

const skillTone = (skill: number | null) =>
  skill == null ? "text-muted" : skill >= 0.2 ? "text-good" : skill >= 0.05 ? "text-accent" : skill > -0.05 ? "text-warn" : "text-bad";

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: string }) {
  return (
    <div className="rounded-xl bg-surface-2 p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className={cx("mt-1 text-xl font-semibold tabular-nums", tone)}>{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
    </div>
  );
}

function Breakdown({ rows }: { rows: { key: string; label: string; stats: ScoreStats }[] }) {
  return (
    <ul className="divide-y divide-line">
      {rows.map(({ key, label, stats }) => (
        <li key={key} className="flex items-center justify-between gap-3 py-2 text-sm">
          <span className="min-w-0 truncate" title={label}>
            {label}
            <span className="ml-1 text-xs text-muted">· {stats.n}</span>
          </span>
          <span className={cx("shrink-0 tabular-nums", skillTone(stats.skill))}>{skillWords(stats.skill)}</span>
        </li>
      ))}
    </ul>
  );
}

const tooltipStyle = { background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 12 };
const monthLabel = (m: string) => new Date(`${m}-15T12:00:00`).toLocaleDateString("es", { month: "short", year: "2-digit" });

export default function Calibration() {
  const [kind, setKind] = useStoredState<"todo" | ForecastKind>("oraqlo.calibration.kind", "todo");
  const [period, setPeriod] = useStoredState<Period>("oraqlo.calibration.period", "todo");
  const [model, setModel] = useStoredState<string>("oraqlo.calibration.model", "");
  const [sourceSort, setSourceSort] = useStoredState<SourceSort>("oraqlo.calibration.sourceSort", "mejor");
  const params = new URLSearchParams();
  if (kind !== "todo") params.set("kind", kind);
  if (period !== "todo") params.set("days", period);
  if (model) params.set("model", model);
  const qs = params.toString();
  const { data, error, isLoading } = useQuery({
    queryKey: ["calibration", qs],
    queryFn: () => api.get<Cal>(`/calibration${qs ? `?${qs}` : ""}`),
    placeholderData: (prev) => prev,
  });
  const s = data?.summary;
  const filtered = kind !== "todo" || period !== "todo" || !!model;

  const kindOptions: Option<"todo" | ForecastKind>[] = [
    { value: "todo", label: "Todo" },
    ...(data?.filters?.kinds ?? []).map((k) => ({ value: k, label: FORECAST_KIND_LABEL[k] })),
  ];
  const points = (data?.reliability ?? [])
    .filter((b) => b.n > 0 && b.observed != null)
    .map((b) => ({ x: Math.round((b.confidence ?? b.p_mid) * 100), y: Math.round((b.observed ?? 0) * 100), n: b.n }));
  const trend = (data?.trend ?? []).filter((t) => t.skill != null).map((t) => ({ ...t, skillPct: Math.round((t.skill ?? 0) * 100) }));
  const sources = [...(data?.sources ?? [])].sort((a, b) =>
    sourceSort === "datos" ? b.n - a.n : sourceSort === "nombre" ? sourceLabel(a.source).localeCompare(sourceLabel(b.source)) : (a.brier ?? 0) - (b.brier ?? 0),
  );
  const sentence = calibrationSentence(s);

  return (
    <div className="space-y-4">
      <ErrorNote error={error} />

      <div className="space-y-2">
        {kindOptions.length > 2 && <ChipGroup label="Tipo" options={kindOptions} value={kind} onChange={setKind} />}
        <div className="flex flex-wrap items-center gap-2">
          <ChipGroup
            label="Periodo"
            options={[
              { value: "todo", label: "Desde siempre" },
              { value: "365", label: "Último año" },
              { value: "90", label: "Últimos 3 meses" },
            ]}
            value={period}
            onChange={setPeriod}
          />
          {(data?.filters?.models.length ?? 0) > 1 && (
            <label className="flex items-center gap-2 text-sm text-muted">
              Modelo
              <select className={cx(inputClass, "w-auto py-1.5")} value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">Todos</option>
                {data!.filters!.models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>

      <Card>
        <SectionTitle aside={<ExportLink />}>¿Qué tal acierta?</SectionTitle>
        {isLoading && <Skeleton className="h-32" />}
        {data && data.n === 0 ? (
          <Empty>
            {filtered
              ? "No hay predicciones resueltas con estos filtros."
              : "Aún no hay datos. Cuando sepas cómo acabó una previsión, márcalo en Predicciones y aquí verás qué tan acertado es Oraqlo."}
            {!filtered && (
              <>
                {" "}
                <Link to="/predicciones" className="text-accent underline">
                  Ir a Predicciones
                </Link>
              </>
            )}
          </Empty>
        ) : (
          s && (
            <>
              <p className="mb-3 text-base font-medium">{sentence}</p>
              <div className="grid grid-cols-2 gap-2">
                <Stat
                  label="Frente al azar"
                  value={s.skill == null ? "—" : `${s.skill >= 0 ? "+" : ""}${Math.round(s.skill * 100)}%`}
                  hint={skillWords(s.skill)}
                  tone={skillTone(s.skill)}
                />
                <Stat
                  label="Acierta lo más probable"
                  value={s.hit_rate == null ? "—" : pct(s.hit_rate)}
                  hint={s.confidence == null ? undefined : `estaba un ${pct(s.confidence)} seguro`}
                />
                <Stat
                  label="Brier medio"
                  value={s.brier == null ? "—" : s.brier.toFixed(3)}
                  hint={s.chance == null ? undefined : `el azar sacaría ${s.chance.toFixed(3)}`}
                />
                <Stat label="Resueltas" value={String(s.n)} hint={`${s.open} pendientes de resolver`} />
              </div>
              <p className="mt-3 text-xs text-muted">
                Brier: 0 es perfecto y menos es mejor. «El azar» es repartir la probabilidad por igual entre los escenarios de cada
                pregunta; superarlo es lo mínimo que se le pide.
              </p>
            </>
          )
        )}
      </Card>

      {data && data.n > 0 && (
        <Card>
          <SectionTitle>Cuando dice un porcentaje, ¿se cumple?</SectionTitle>
          <p className="mb-3 text-sm text-muted">
            Cada punto agrupa previsiones con una confianza parecida en lo que veían más probable. Sobre la diagonal es perfecto; por
            debajo, se pasó de confiado. Más grande = más previsiones.
          </p>
          <div className="h-64" role="img" aria-label={`Diagrama de fiabilidad. ${sentence}`}>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 8, right: 12, bottom: 14, left: 0 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis
                  type="number"
                  dataKey="x"
                  domain={[0, 100]}
                  unit="%"
                  stroke="var(--muted)"
                  fontSize={12}
                  label={{ value: "Lo seguro que estaba", position: "insideBottom", offset: -2, fill: "var(--muted)", fontSize: 12 }}
                />
                <YAxis
                  type="number"
                  dataKey="y"
                  domain={[0, 100]}
                  unit="%"
                  stroke="var(--muted)"
                  fontSize={12}
                  label={{ value: "Cuántas acertó", angle: -90, position: "insideLeft", offset: 22, fill: "var(--muted)", fontSize: 12 }}
                />
                <ZAxis dataKey="n" range={[40, 400]} />
                <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]} stroke="var(--muted)" strokeDasharray="4 4" />
                <Tooltip
                  contentStyle={tooltipStyle}
                  content={({ payload }) => {
                    const p = payload?.[0]?.payload as { x: number; y: number; n: number } | undefined;
                    return p ? (
                      <div style={tooltipStyle} className="p-2 text-xs">
                        {p.n} previsión{p.n === 1 ? "" : "es"} con ~{p.x}% de seguridad: acertó el {p.y}%.
                      </div>
                    ) : null;
                  }}
                />
                <Scatter data={points} fill="var(--accent)" />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      {trend.length >= 2 && (
        <Card>
          <SectionTitle>¿Está mejorando?</SectionTitle>
          <p className="mb-3 text-sm text-muted">Cuánto mejor que el azar acierta, mes a mes (por fecha de resolución).</p>
          <div className="h-48" role="img" aria-label="Evolución mensual de la habilidad frente al azar">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trend} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis dataKey="month" tickFormatter={monthLabel} stroke="var(--muted)" fontSize={12} />
                <YAxis unit="%" stroke="var(--muted)" fontSize={12} width={44} />
                <ReferenceLine y={0} stroke="var(--muted)" strokeDasharray="4 4" />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v) => [`${v}% frente al azar`, ""]}
                  labelFormatter={(m) => monthLabel(String(m))}
                />
                <Line type="monotone" dataKey="skillPct" stroke="var(--accent)" strokeWidth={2} dot />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      {data && data.n > 0 && (
        <div className="grid gap-4 sm:grid-cols-2">
          {(data.by_kind?.length ?? 0) > 1 && (
            <Card>
              <SectionTitle>Por tipo</SectionTitle>
              <Breakdown rows={data.by_kind!.map((k) => ({ key: k.kind, label: FORECAST_KIND_LABEL[k.kind], stats: k }))} />
            </Card>
          )}
          {(data.by_horizon?.length ?? 0) > 1 && (
            <Card>
              <SectionTitle>Por plazo</SectionTitle>
              <Breakdown rows={data.by_horizon!.map((h) => ({ key: h.horizon, label: h.label, stats: h }))} />
            </Card>
          )}
        </div>
      )}

      {data && (data.worst?.length ?? 0) > 0 && (
        <Card>
          <SectionTitle>Dónde más falló</SectionTitle>
          <ul className="divide-y divide-line">
            {data.worst!.map((w) => (
              <li key={w.id} className="py-2 text-sm">
                <p className="line-clamp-2">{w.question}</p>
                <p className="text-xs text-muted">
                  Dijo <b className="text-fg">{w.predicted}</b> ({pct(w.predicted_p)}) y pasó <b className="text-fg">{w.actual}</b> (le daba{" "}
                  {pct(w.actual_p)}) · {fmtDate(w.resolved_at)}
                </p>
              </li>
            ))}
          </ul>
          <Link to="/predicciones" className="mt-2 inline-block text-sm text-accent underline">
            Ver todas en Predicciones
          </Link>
        </Card>
      )}

      {sources.length > 0 && (
        <Card>
          <SectionTitle aside={sources.length > 2 ? <SortSelect options={SOURCE_SORTS} value={sourceSort} onChange={setSourceSort} /> : null}>
            Por modelo y fuente
          </SectionTitle>
          <Breakdown rows={sources.map((x) => ({ key: x.source, label: sourceLabel(x.source), stats: x }))} />
        </Card>
      )}

      {data?.recalibration && data.recalibration.length > 0 && (
        <Card>
          <SectionTitle>Corrección automática</SectionTitle>
          <p className="mb-2 text-sm text-muted">
            Con lo que ya se resolvió, Oraqlo aprende si cada modelo se pasa de confiado o se queda corto, y corrige sus previsiones
            nuevas en consecuencia.
          </p>
          <ul className="divide-y divide-line">
            {data.recalibration.map((r) => (
              <li key={r.model} className="py-2 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">{r.model}</span>
                  <span className={r.active ? "text-accent" : "text-muted"}>
                    {r.active ? `activa · a=${r.a.toFixed(2)}` : r.n < r.min_resolved ? `${r.n}/${r.min_resolved}` : "sin corrección"}
                  </span>
                </div>
                <p className="text-xs text-muted">
                  {r.summary}
                  {r.active &&
                    r.brier_before != null &&
                    r.brier_after != null &&
                    ` Brier en su historial: ${r.brier_before.toFixed(3)} → ${r.brier_after.toFixed(3)}.`}
                </p>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

const SOURCE_SORTS: Option<SourceSort>[] = [
  { value: "mejor", label: "Mejor acierto" },
  { value: "datos", label: "Más datos" },
  { value: "nombre", label: "Nombre" },
];
