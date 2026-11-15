// Validación en vivo del caso (espejo ligero de oraqlo.casefile.parse_case;
// el servidor vuelve a validar siempre antes de ejecutar).

export interface CaseData {
  question?: string;
  horizon_days?: number;
  actions?: Record<string, { description?: string; outcomes?: Record<string, number> }>;
  utilities?: Record<string, number>;
  risk?: Record<string, unknown>;
  depth?: number;
}

export function validateCase(c: CaseData): string[] {
  const issues: string[] = [];
  if (!c.question || !String(c.question).trim()) issues.push('Falta "question".');
  const actions = c.actions ?? {};
  if (Object.keys(actions).length === 0) issues.push('Define al menos una acción en "actions".');
  const utilities = c.utilities ?? {};
  for (const [id, action] of Object.entries(actions)) {
    const outcomes = action.outcomes ?? {};
    const labels = Object.keys(outcomes);
    if (labels.length === 0) {
      issues.push(`La acción "${id}" no tiene resultados.`);
      continue;
    }
    const total = Object.values(outcomes).reduce((a, b) => a + Number(b), 0);
    if (Math.abs(total - 1) > 0.01) issues.push(`Las probabilidades de "${id}" suman ${total.toFixed(2)}, no 1.`);
    for (const label of labels) {
      if (!(label in utilities)) issues.push(`Falta la utilidad del resultado "${label}".`);
    }
  }
  return issues;
}
