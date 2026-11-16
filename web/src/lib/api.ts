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

export interface Calibration {
  n: number;
  sources: { source: string; n: number; brier: number }[];
  reliability: { bin: number; p_mid: number; n: number; observed: number | null }[];
}

export interface PracticeState {
  auto: boolean;
  interval_min: number;
  last_run: string | null;
  next_run: string | null;
  running: boolean;
  reliability_oraqlo: number | null;
  reliability_market: number | null;
  advice: string | null;
  recent: Job[];
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
}

export type UpdateMode = ConfigView["updates_mode"];

/** Nombres amables y dónde conseguir la clave de cada proveedor. */
export const PROVIDER_INFO: Record<string, { label: string; keyUrl?: string; note?: string }> = {
  anthropic: { label: "Claude (Anthropic)", keyUrl: "https://console.anthropic.com/settings/keys", note: "Recomendado" },
  openai: { label: "OpenAI (ChatGPT)", keyUrl: "https://platform.openai.com/api-keys" },
  gemini: { label: "Google Gemini", keyUrl: "https://aistudio.google.com/apikey" },
  openrouter: { label: "OpenRouter", keyUrl: "https://openrouter.ai/keys", note: "Muchos modelos con una clave" },
  "ollama-cloud": { label: "Ollama Cloud", keyUrl: "https://ollama.com/settings/keys" },
  "ollama-local": { label: "Ollama en este PC", note: "Gratis y sin clave; necesita Ollama abierto" },
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
  red_team: "Red Team",
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

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* respuesta sin JSON */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: (path: string) => request<void>("DELETE", path),
};

export type AskMode = "forecast" | "full" | "goal";

export const ask = (question: string, mode: AskMode, horizon_days: number, research: boolean) =>
  api.post<{ job_id: string }>("/ask", { question, mode, horizon_days, research });

export const pct = (p: number | null | undefined, digits = 0) =>
  p == null ? "—" : `${(p * 100).toFixed(digits)}%`;

export const fmtDate = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("es", { dateStyle: "medium", timeStyle: "short" }) : "—";
