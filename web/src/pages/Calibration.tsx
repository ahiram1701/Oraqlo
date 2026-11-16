import { useQuery } from "@tanstack/react-query";
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, ReferenceLine } from "recharts";
import { Card, Empty, ErrorNote, SectionTitle, Skeleton } from "../components/ui";
import { api, sourceLabel, type Calibration as Cal } from "../lib/api";

export default function Calibration() {
  const { data, error, isLoading } = useQuery({ queryKey: ["calibration"], queryFn: () => api.get<Cal>("/calibration") });
  const points = (data?.reliability ?? [])
    .filter((b) => b.n > 0 && b.observed != null)
    .map((b) => ({ x: Math.round(b.p_mid * 100), y: Math.round((b.observed ?? 0) * 100), n: b.n }));
  // Una frase que resume el gráfico: ¿se pasa de confiado o se queda corto?
  const weighted = points.reduce((acc, p) => acc + (p.y - p.x) * p.n, 0);
  const totalN = points.reduce((acc, p) => acc + p.n, 0);
  const gap = totalN ? weighted / totalN : 0;
  const summary =
    totalN < 5
      ? "Aún hay pocas predicciones resueltas para sacar conclusiones."
      : Math.abs(gap) < 6
        ? "Bien calibrado: cuando dice un porcentaje, suele cumplirse más o menos así."
        : gap < 0
          ? "Algo confiado de más: lo que da por probable pasa menos de lo que dice."
          : "Algo prudente de más: lo que ve poco probable pasa más de lo que dice.";

  return (
    <div className="space-y-4">
      <ErrorNote error={error} />
      <Card>
        <SectionTitle>¿Acierta cuando dice un porcentaje?</SectionTitle>
        {data && data.n > 0 && <p className="mb-2 text-base font-medium">{summary}</p>}
        <p className="mb-3 text-sm text-muted">
          Si Oraqlo dice «70%», debería pasar 7 de cada 10 veces. Cada punto agrupa predicciones parecidas: sobre la diagonal
          es perfecto; por debajo, se pasó de confiado.
        </p>
        {isLoading && <Skeleton className="h-64" />}
        {!data ? null : data.n === 0 ? (
          <Empty>Aún no hay datos. Cuando sepas cómo acabó una previsión, márcalo en Predicciones y aquí verás qué tan acertado es Oraqlo.</Empty>
        ) : (
          <div className="h-64" role="img" aria-label={`Diagrama de fiabilidad. ${summary}`}>
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
                  name="Predicho"
                  label={{ value: "Lo que dijo", position: "insideBottom", offset: -2, fill: "var(--muted)", fontSize: 12 }}
                />
                <YAxis
                  type="number"
                  dataKey="y"
                  domain={[0, 100]}
                  unit="%"
                  stroke="var(--muted)"
                  fontSize={12}
                  name="Observado"
                  label={{ value: "Lo que pasó", angle: -90, position: "insideLeft", offset: 22, fill: "var(--muted)", fontSize: 12 }}
                />
                <ZAxis dataKey="n" range={[40, 400]} name="n" />
                <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]} stroke="var(--muted)" strokeDasharray="4 4" />
                <Tooltip contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 12 }} />
                <Scatter data={points} fill="var(--accent)" />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>
      <Card>
        <SectionTitle>Error por fuente</SectionTitle>
        {data && data.sources.length === 0 && <Empty>Sin datos todavía.</Empty>}
        <ul className="divide-y divide-line">
          {data?.sources.map((s) => (
            <li key={s.source} className="flex items-center justify-between py-2 text-sm">
              <span className="truncate" title={s.source}>
                {sourceLabel(s.source)}
              </span>
              <span className="tabular-nums text-muted">
                <b className="text-fg">{s.brier.toFixed(3)}</b> · n={s.n}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-muted">Puntuación Brier: 0 = perfecto · 0,25 = tirar una moneda · 2 = seguro y equivocado.</p>
      </Card>
      {data?.recalibration && data.recalibration.length > 0 && (
        <Card>
          <SectionTitle>Corrección automática</SectionTitle>
          <p className="mb-2 text-sm text-muted">
            Con lo que ya se resolvió, Oraqlo aprende si cada modelo se pasa de confiado o se queda corto, y corrige sus
            previsiones nuevas en consecuencia.
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
                  {r.active && r.brier_before != null && r.brier_after != null &&
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
