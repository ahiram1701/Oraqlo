import { useId } from "react";
import { cx, inputClass } from "./ui";

/** Plazos frecuentes, en días. */
export const PRESETS: { days: number; label: string }[] = [
  { days: 7, label: "1 semana" },
  { days: 30, label: "1 mes" },
  { days: 90, label: "3 meses" },
  { days: 180, label: "6 meses" },
  { days: 365, label: "1 año" },
  { days: 730, label: "2 años" },
  { days: 1825, label: "5 años" },
  { days: 3650, label: "10 años" },
];

export const MAX_DAYS = 3650; // el mismo límite que acepta el servidor (AskIn.horizon_days)

export type Unit = "dias" | "semanas" | "meses" | "anios" | "fecha";

export const UNITS: { id: Unit; label: string; days: number }[] = [
  { id: "dias", label: "días", days: 1 },
  { id: "semanas", label: "semanas", days: 7 },
  { id: "meses", label: "meses", days: 30 },
  { id: "anios", label: "años", days: 365 },
  { id: "fecha", label: "hasta una fecha", days: 0 },
];

/** Elección del usuario: un plazo de la lista o uno propio («Otro…»). */
export interface Horizon {
  preset: number | null; // días del plazo elegido; null = propio
  amount: number;
  unit: Unit;
  date: string; // AAAA-MM-DD, para la unidad «fecha»
}

/** AAAA-MM-DD en el calendario local (toISOString daría el día UTC: por la
 * tarde-noche en América, «mañana» salía como pasado mañana). */
export function localISODate(d: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function isoInDays(days: number, from = new Date()) {
  const d = new Date(from);
  d.setDate(d.getDate() + days);
  return localISODate(d);
}

export function daysUntil(iso: string) {
  const ms = new Date(`${iso}T23:59:59`).getTime() - Date.now();
  return Math.min(MAX_DAYS, Math.max(1, Math.ceil(ms / 86_400_000)));
}

/** Días que se mandan al servidor, siempre entre 1 y 3650. */
export function horizonDays(h: Horizon): number {
  if (h.preset != null) return h.preset;
  if (h.unit === "fecha") return daysUntil(h.date);
  const per = UNITS.find((u) => u.id === h.unit)?.days ?? 1;
  const days = Math.round((Number.isFinite(h.amount) ? h.amount : 1) * per);
  return Math.min(MAX_DAYS, Math.max(1, days));
}

/** Un horizonte en días como elección: de la lista si coincide, si no propio. */
export function fromDays(days: number): Horizon {
  const preset = PRESETS.find((p) => p.days === days);
  if (preset) return { preset: days, amount: 1, unit: "meses", date: isoInDays(90) };
  for (const unit of ["anios", "meses", "semanas"] as const) {
    const per = UNITS.find((u) => u.id === unit)!.days;
    if (days % per === 0) return { preset: null, amount: days / per, unit, date: isoInDays(days) };
  }
  return { preset: null, amount: days, unit: "dias", date: isoInDays(days) };
}

/** Texto corto: «2 años», «45 días», «hasta el 3 de marzo de 2027». */
export function describeHorizon(h: Horizon): string {
  if (h.preset != null) return PRESETS.find((p) => p.days === h.preset)?.label ?? `${h.preset} días`;
  if (h.unit === "fecha") {
    const d = new Date(`${h.date}T12:00:00`);
    return Number.isNaN(d.getTime()) ? "—" : `hasta el ${d.toLocaleDateString("es", { day: "numeric", month: "long", year: "numeric" })}`;
  }
  return `${horizonDays(h)} días`;
}

export function HorizonPicker({ value, onChange }: { value: Horizon; onChange: (h: Horizon) => void }) {
  const id = useId();
  const custom = value.preset == null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
      <label htmlFor={`${id}-plazo`} className="whitespace-nowrap">
        Pensando a
      </label>
      <select
        id={`${id}-plazo`}
        className={cx(inputClass, "w-auto py-2")}
        value={custom ? "otro" : String(value.preset)}
        onChange={(e) =>
          onChange(
            e.target.value === "otro"
              ? { ...value, preset: null, amount: value.amount || 1, unit: value.unit || "meses" }
              : { ...value, preset: Number(e.target.value) },
          )
        }
      >
        {PRESETS.map((p) => (
          <option key={p.days} value={p.days}>
            {p.label}
          </option>
        ))}
        <option value="otro">Otro…</option>
      </select>
      {custom && (
        // Cantidad y unidad en la misma fila, también en el celular.
        <div className="flex min-w-0 basis-full items-center gap-2 sm:basis-auto">
          {value.unit === "fecha" ? (
            <input
              type="date"
              aria-label="Fecha"
              className={cx(inputClass, "min-w-0 flex-1 py-2")}
              value={value.date}
              min={isoInDays(1)}
              max={isoInDays(MAX_DAYS)}
              onChange={(e) => onChange({ ...value, date: e.target.value || isoInDays(90) })}
            />
          ) : (
            <input
              type="number"
              inputMode="numeric"
              aria-label="Cantidad"
              min={1}
              className={cx(inputClass, "py-2")}
              style={{ width: "5.5rem", flex: "none" }}
              value={value.amount}
              onChange={(e) => onChange({ ...value, amount: Math.max(1, Number(e.target.value) || 1) })}
            />
          )}
          <select
            aria-label="Unidad"
            className={cx(inputClass, "min-w-0 flex-1 py-2")}
            value={value.unit}
            onChange={(e) => onChange({ ...value, unit: e.target.value as Unit })}
          >
            {UNITS.map((u) => (
              <option key={u.id} value={u.id}>
                {u.label}
              </option>
            ))}
          </select>
          {horizonDays(value) === MAX_DAYS && <span className="shrink-0 text-xs">Máx. 10 años</span>}
        </div>
      )}
    </div>
  );
}
