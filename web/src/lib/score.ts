// Una sola escala para «qué tal acierta», en todas las pantallas: cuánto mejor que
// el azar (repartir la probabilidad por igual). El Brier queda como dato técnico.

/** «34% mejor que el azar», en palabras. */
export function skillWords(skill: number | null | undefined): string {
  if (skill == null) return "—";
  if (skill >= 0.05) return `${Math.round(skill * 100)}% mejor que el azar`;
  if (skill <= -0.05) return `${Math.round(-skill * 100)}% peor que el azar`;
  return "igual que el azar";
}

/** «+34%» / «−20%» (la cifra grande). */
export function fmtSkill(skill: number | null | undefined): string {
  if (skill == null) return "—";
  const n = Math.round(skill * 100);
  return n > 0 ? `+${n}%` : n < 0 ? `−${-n}%` : "0%";
}

/** Brier con 2 decimales en todas partes (0 = perfecto, menos es mejor). */
export function fmtBrier(brier: number | null | undefined): string {
  return brier == null ? "—" : brier.toFixed(2);
}

export const skillTone = (skill: number | null | undefined) =>
  skill == null ? "text-muted" : skill >= 0.2 ? "text-good" : skill >= 0.05 ? "text-accent" : skill > -0.05 ? "text-warn" : "text-bad";
