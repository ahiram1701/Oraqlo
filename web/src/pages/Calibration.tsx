import { useQuery } from "@tanstack/react-query";
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, ReferenceLine } from "recharts";
import { Card, Empty, ErrorNote, SectionTitle } from "../components/ui";
import { api, type Calibration as Cal } from "../lib/api";

export default function Calibration() {
  const { data, error } = useQuery({ queryKey: ["calibration"], queryFn: () => api.get<Cal>("/calibration") });
  const points = (data?.reliability ?? [])
    .filter((b) => b.n > 0 && b.observed != null)
    .map((b) => ({ x: Math.round(b.p_mid * 100), y: Math.round((b.observed ?? 0) * 100), n: b.n }));

  return (
    <div className="space-y-4">
      <ErrorNote error={error} />
      <Card>
        <SectionTitle>Diagrama de fiabilidad</SectionTitle>
        <p className="mb-3 text-sm text-muted">
          Cuando Oraqlo dice “70%”, ¿pasa el 70% de las veces? Los puntos sobre la diagonal indican calibración perfecta;
          por debajo, exceso de confianza.
        </p>
        {data && data.n === 0 ? (
          <Empty>Resuelve predicciones en el Ledger para ver la calibración.</Empty>
        ) : (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: -16 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis type="number" dataKey="x" domain={[0, 100]} unit="%" stroke="var(--muted)" fontSize={11} name="Predicho" />
                <YAxis type="number" dataKey="y" domain={[0, 100]} unit="%" stroke="var(--muted)" fontSize={11} name="Observado" />
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
        <SectionTitle>Brier por fuente</SectionTitle>
        {data && data.sources.length === 0 && <Empty>Sin datos todavía.</Empty>}
        <ul className="divide-y divide-line">
          {data?.sources.map((s) => (
            <li key={s.source} className="flex items-center justify-between py-2 text-sm">
              <span className="truncate">{s.source}</span>
              <span className="tabular-nums text-muted">
                <b className="text-fg">{s.brier.toFixed(3)}</b> · n={s.n}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-muted">0 = perfecto · 0.25 = lanzar una moneda con 50% · 2 = certeza en lo equivocado.</p>
      </Card>
    </div>
  );
}
