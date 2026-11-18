// Cliente tipado de la API de Oraqlo (espejo de src/oraqlo/api/app.py).

export type Outcomes = Record<string, number>;

export interface Forecast {
  id: string;
  question: string;
  source: string;
  outcomes: Outcomes;
  point: number | null;
  interval: [number, number] | null;
  horizon_days: number;
  assumptions: string[];
  created_at: string | null;
}

export interface ResolvedForecast extends Forecast {
  actual_outcome: string;
  brier: number;
  resolved_at: string;
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Job<R = any> {
  id: string;
  kind: string;
  status: JobStatus;
  params: Record<string, unknown>;
  error: string | null;
  run_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  result?: R | null;
}

export interface JobEvent {
  seq: number;
  type: string;
  data: any;
}

export interface Milestone {
  text: string;
  target_date: string;
  signal: string;
  status: "pendiente" | "cumplido" | "fallado";
}

export interface CheckIn {
  date: string;
  note: string;
  p_estimate: number;
  comment: string;
  replan_recommended: boolean;
}

export interface Goal {
  id: string;
  goal: string;
  created_at: string;
  deadline: string;
  summary: string;
  steps: { order: number; action: string; when: string; why: string }[];
  milestones: Milestone[];
  checkins: CheckIn[];
  baseline_p: number;
  initial_p: number;
  current_p: number;
  trajectory: number[];
  days_left: number;
  status: "activo" | "logrado" | "no-logrado" | "abandonado";
  plan_version: number;
  overdue: number[];
}

export interface ScoreStats {
  n: number;
  /** Brier medio (multiclase, 0 = perfecto). */
  brier: number | null;
  /** Brier de repartir por igual entre los escenarios de cada pregunta. */
  chance: number | null;
  /** 1 − brier/chance: 0 = como el azar, 1 = perfecto, negativo = peor que el azar. */
  skill: number | null;
}

export interface Calibration {
  n: number;
  filters?: { kinds: ForecastKind[]; models: string[] };
  summary?: ScoreStats & {
    log_loss: number | null;
    hit_rate: number | null;
    confidence: number | null;
    overconfidence: number | null;
    open: number;
  };
  /** Un punto por previsión: confianza en lo más probable frente a cuántas veces acertó. */
  reliability: { bin: number; p_mid: number; confidence: number | null; n: number; observed: number | null }[];
  trend?: (ScoreStats & { month: string })[];
  by_kind?: (ScoreStats & { kind: ForecastKind })[];
  by_horizon?: (ScoreStats & { horizon: string; label: string })[];
  /** `reference`: el mercado de Polymarket, solo para comparar (no cuenta en el resto). */
  sources: (ScoreStats & { source: string; reference?: boolean })[];
  worst?: {
    id: string;
    question: string;
    source: string;
    predicted: string;
    predicted_p: number;
    actual: string;
    actual_p: number;
    brier: number;
    resolved_at: string;
  }[];
  /** Corrección aprendida por modelo: p' ∝ p^a (a < 1 suaviza, a > 1 concentra). */
  recalibration?: {
    model: string;
    a: number;
    n: number;
    active: boolean;
    min_resolved: number;
    brier_before: number | null;
    brier_after: number | null;
    summary: string;
  }[];
}

/** Nombre legible de una fuente del ledger (`llm:<modelo>[:tipo]`, `polymarket:mercado`…). */
export function sourceLabel(source: string): string {
  if (source === "polymarket:mercado") return "Mercado (Polymarket), para comparar";
  if (!source.startsWith("llm:")) return source;
  const rest = source.slice(4);
  for (const [suffix, label] of [
    [":prevision", "previsiones"],
    [":objetivo", "objetivos"],
    [":panel", "análisis completo"],
  ] as const) {
    if (rest.endsWith(suffix)) return `${rest.slice(0, -suffix.length)} · ${label}`;
  }
  return `${rest} · práctica`;
}

export interface PracticeState {
  auto: boolean;
  interval_min: number;
  last_run: string | null;
  next_run: string | null;
  running: boolean;
  /** ¿Investiga en internet antes de predecir? (necesita la clave de Ollama) */
  research?: boolean;
  advice: string | null;
  recent: Job[];
  duels?: { items: Duel[]; summary: DuelSummary };
}

/** Oraqlo frente al mercado en una pregunta de Práctica. */
export interface Duel {
  market_id: string;
  question: string;
  /** El resultado que se compara: el que ganó o, si sigue abierto, el «sí». */
  label: string;
  oraqlo_p: number;
  market_p: number;
  created_at: string | null;
  closes_at: string | null;
  status: "resuelto" | "abierto";
  actual: string | null;
  resolved_at: string | null;
  oraqlo_brier: number | null;
  market_brier: number | null;
  chance_brier: number | null;
  winner: "oraqlo" | "mercado" | "empate" | null;
  /** El mercado ya daba el resultado por hecho al predecir: no cuenta. */
  already_decided?: boolean;
}

export interface DuelSummary {
  resolved: number;
  open: number;
  won: number;
  lost: number;
  tied: number;
  already_decided?: number;
  oraqlo_brier: number | null;
  market_brier: number | null;
  chance_brier: number | null;
  /** 1 − brier/azar, la misma escala que Calibración. */
  oraqlo_skill?: number | null;
  market_skill?: number | null;
}

/** La pregunta sin la etiqueta interna del mercado («[polymarket:123] …»). */
export function displayQuestion(question: string): string {
  return question.replace(/^\[polymarket:[^\]]*\]\s*/, "");
}

export interface RunSummary {
  id: string;
  kind: "forecast" | "cycle" | "goal";
  title: string;
  created_at: string;
}

export interface ProviderEntry {
  name: string;
  kind: string;
  api_key_env: string | null;
  key_present: boolean | null;
  key_hint: string | null;
}

export interface RoleView {
  name: string;
  provider: string;
  model: string;
  temperature: number;
  effort: string | null;
}

export type RoleSettings = { provider?: string | null; model?: string | null; temperature?: number | null; effort?: string | null };

export interface ProvidersView {
  providers: ProviderEntry[];
  roles: RoleView[];
  /** Lo fijado a mano por rol en la configuración (sin lo heredado). */
  configured: Record<string, RoleSettings>;
  /** Proveedor y modelo elegidos solos, según las claves, si «Todos» no fija proveedor. */
  auto?: { provider: string; model: string };
  /** Modelo recomendado de cada proveedor. */
  default_models?: Record<string, string>;
  /** Clave de la búsqueda web (ollama.com): sirve con cualquier proveedor de IA. */
  web_search?: { api_key_env: string; key_present: boolean; key_hint: string | null };
}

export interface SetupStatus {
  ready: boolean;
  needs_setup: boolean;
  local: boolean;
  legacy_imported: boolean;
}

export interface ConfigView {
  roles: Record<string, RoleSettings>;
  updates_mode: "auto" | "notify" | "off";
  allowed_users: string[];
}

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
  fix: string | null;
  /** Función opcional: si falla se avisa, pero no es un error. */
  optional?: boolean;
}

export type UpdateMode = ConfigView["updates_mode"];

/** Nombres amables y dónde conseguir la clave de cada proveedor. */
/** Modelos de un proveedor: nombres y, en Ollama, el catálogo con los de la nube. */
export interface CatalogModel {
  name: string;
  cloud: boolean;
  size?: number | null;
  installed: boolean;
}
export interface ModelList {
  models: string[];
  catalog?: CatalogModel[];
}

export const PROVIDER_INFO: Record<string, { label: string; keyUrl?: string; note?: string }> = {
  anthropic: { label: "Claude (Anthropic)", keyUrl: "https://console.anthropic.com/settings/keys", note: "Recomendado" },
  openai: { label: "OpenAI (ChatGPT)", keyUrl: "https://platform.openai.com/api-keys" },
  gemini: { label: "Google Gemini", keyUrl: "https://aistudio.google.com/apikey" },
  openrouter: { label: "OpenRouter", keyUrl: "https://openrouter.ai/keys", note: "Muchos modelos con una clave" },
  "ollama-cloud": {
    label: "Ollama Cloud",
    keyUrl: "https://ollama.com/settings/keys",
    note: "Modelos en la nube con la clave de tu Cuenta de Ollama; no hace falta instalar nada",
  },
  "ollama-local": {
    label: "Ollama en este PC",
    note: "Gratis y sin clave; con la sesión iniciada en la app de Ollama también usa modelos «:cloud»",
  },
  mistral: { label: "Mistral", keyUrl: "https://console.mistral.ai/api-keys" },
  xai: { label: "xAI (Grok)", keyUrl: "https://console.x.ai" },
  deepseek: { label: "DeepSeek", keyUrl: "https://platform.deepseek.com/api_keys" },
  groq: { label: "Groq", keyUrl: "https://console.groq.com/keys" },
  lmstudio: { label: "LM Studio en este PC", note: "Sin clave; necesita LM Studio abierto" },
};

export const providerLabel = (name: string) => PROVIDER_INFO[name]?.label ?? name;

export const ROLE_LABELS: Record<string, string> = {
  default: "Todos (por defecto)",
  estratega: "Estratega",
  red_team: "Abogado del diablo",
  oraculo: "Oráculo",
  juez: "Juez",
  investigador: "Investigador",
  redactor: "Redactor",
  practica: "Práctica",
};

export interface Health {
  app?: string;
  status: string;
  version: string;
  db_version: number;
  uptime_s: number;
  active_jobs: number;
  update?: {
    available: string | null;
    staged?: string | null;
    mode: string;
    managed?: boolean;
    last_check: string | null;
    last_result?: { status: string; detail: string } | null;
  } | null;
}

export type TailscaleState = "missing" | "stopped" | "needs_login" | "running";

export interface ServerInfo {
  port: number | null;
  local_url: string | null;
  managed: boolean;
  tailscale: {
    installed: boolean;
    state: TailscaleState;
    enabled: boolean;
    https_port?: number;
    url: string | null;
    serving_port: number | null;
  };
}

export interface PortChangeResult {
  port: number;
  restarting: boolean;
  local_url?: string;
  detail: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const FIELD_NAMES: Record<string, string> = {
  question: "La pregunta",
  note: "La nota",
  value: "La clave",
  port: "El puerto",
  name: "El nombre",
  outcome: "El resultado",
  horizon_days: "El horizonte",
  interval_min: "El intervalo",
  case: "El caso",
};

interface ValidationIssue {
  loc?: (string | number)[];
  msg?: string;
  type?: string;
  ctx?: Record<string, unknown>;
}

/** Los 422 de FastAPI traen una lista en inglés: se convierte en una frase en español. */
export function describeValidation(detail: unknown): string {
  if (!Array.isArray(detail) || detail.length === 0) return "Hay datos que no son válidos.";
  const issue = detail[0] as ValidationIssue;
  const field = String(issue.loc?.filter((x) => x !== "body").at(-1) ?? "");
  const who = FIELD_NAMES[field] ?? (field ? `El campo «${field}»` : "Un dato");
  const ctx = issue.ctx ?? {};
  switch (issue.type) {
    case "string_too_long":
      return `${who} es demasiado larga: máximo ${ctx.max_length} caracteres.`;
    case "string_too_short":
      return ctx.min_length === 1 ? `${who} no puede estar vacía.` : `${who} es demasiado corta: mínimo ${ctx.min_length} caracteres.`;
    case "missing":
      return `${who} es obligatoria.`;
    case "greater_than_equal":
    case "greater_than":
      return `${who} debe ser al menos ${ctx.ge ?? ctx.gt}.`;
    case "less_than_equal":
    case "less_than":
      return `${who} debe ser como mucho ${ctx.le ?? ctx.lt}.`;
    case "int_parsing":
    case "float_parsing":
      return `${who} debe ser un número.`;
    default:
      return `${who} no es válida.`;
  }
}

/** Mensaje claro según el código: el detalle del servidor solo cuando ayuda. */
export function friendlyError(status: number, detail: string): string {
  if (status === 502 || status === 503 || status === 504)
    return "Oraqlo no responde ahora mismo (puede estar reiniciándose). Espera unos segundos y reintenta.";
  if (status === 401 || status === 403) return detail || "No tienes permiso para hacer esto desde aquí.";
  if (status === 404) return detail && detail !== "Not Found" ? detail : "No se encontró: puede que ya no exista.";
  if (status === 429) return "Demasiadas peticiones seguidas. Espera un momento y reintenta.";
  if (status >= 500) return `Algo falló dentro de Oraqlo${detail && detail !== "Internal Server Error" ? `: ${detail}` : "."}`;
  return detail;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "No se pudo conectar con Oraqlo. Comprueba que el PC esté encendido y con conexión.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : describeValidation(data.detail);
    } catch {
      /* respuesta sin JSON */
    }
    throw new ApiError(res.status, friendlyError(res.status, detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: (path: string) => request<void>("DELETE", path),
};

export type AskMode = "forecast" | "full" | "goal";

export const ask = (question: string, mode: AskMode, horizon_days: number, research: boolean) =>
  api.post<{ job_id: string }>("/ask", { question, mode, horizon_days, research });

/** Una pregunta de Oraqlo al usuario, cuando internet no basta. */
export interface ClarifyItem {
  id: string;
  text: string;
  why: string;
  options: string[];
  answer?: string | null;
}

/** Resultado de un análisis en pausa: espera tus respuestas. */
export interface QuestionsResult {
  kind: "questions";
  mode: AskMode;
  question: string;
  questions: ClarifyItem[];
  /** Si ya se respondió (quizá desde otro dispositivo): el análisis que siguió. */
  answered_by?: string;
}

export const isQuestions = (result: unknown): result is QuestionsResult =>
  (result as { kind?: string } | null)?.kind === "questions";

/** Responde (o, con `null`, sigue sin responder) y devuelve el análisis que continúa. */
export const answerJob = (jobId: string, answers: Record<string, string> | null) =>
  api.post<{ job_id: string }>(`/jobs/${jobId}/answer`, { answers });

export const pct = (p: number | null | undefined, digits = 0) =>
  p == null ? "—" : `${(p * 100).toFixed(digits)}%`;

/**
 * Fecha del servidor. Las que pone SQLite por defecto («2026-11-18 02:51:07») están en
 * UTC pero sin zona, y el navegador las leía como hora local: salían desfasadas.
 */
export function parseDate(iso: string): Date {
  return new Date(/^\d{4}-\d\d-\d\d \d\d:\d\d(:\d\d)?$/.test(iso) ? `${iso.replace(" ", "T")}Z` : iso);
}

export const fmtDate = (iso: string | null | undefined) =>
  iso ? parseDate(iso).toLocaleString("es", { dateStyle: "medium", timeStyle: "short" }) : "—";

/** Tipo de predicción según su fuente: qué la produjo y quién la resuelve. */
export type ForecastKind = "prevision" | "objetivo" | "panel" | "practica";

export function forecastKind(source: string, question = ""): ForecastKind {
  // La Práctica se reconoce por la etiqueta del mercado: antes de la 0.7 todas las
  // previsiones eran `llm:<modelo>`, sin tipo, y no son de Práctica.
  if (source.startsWith("polymarket:") || question.startsWith("[polymarket:")) return "practica";
  for (const kind of ["prevision", "objetivo", "panel"] as const) if (source.endsWith(`:${kind}`)) return kind;
  return "prevision";
}

export const FORECAST_KIND_LABEL: Record<ForecastKind, string> = {
  prevision: "Previsiones",
  objetivo: "Objetivos",
  panel: "Análisis",
  practica: "Práctica",
};
