import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, type Check, type ConfigView, type Health, type UpdateMode } from "../lib/api";
import { LOCAL_ONLY_NOTE, useSetup } from "../lib/setup";
import { Badge, Button, Card, ErrorNote, SectionTitle, cx, inputClass, useFeedback } from "./ui";

const MODES: { value: UpdateMode; label: string; hint: string }[] = [
  { value: "auto", label: "Automáticas", hint: "Se instalan solas cuando no hay análisis en curso." },
  { value: "notify", label: "Avisarme", hint: "Te avisa y tú decides cuándo instalar." },
  { value: "off", label: "Desactivadas", hint: "No busca versiones nuevas." },
];

/** Espera a que el servidor vuelva tras reiniciarse (misma dirección) y recarga. */
async function reloadWhenBack() {
  await new Promise((r) => setTimeout(r, 2500));
  for (let i = 0; i < 90; i++) {
    try {
      const res = await fetch("/api/health", { cache: "no-store" });
      if (res.ok) return window.location.reload();
    } catch {
      /* reiniciando */
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

export function UpdatesCard() {
  const { confirm } = useFeedback();
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<{ login: string | null; via: string }>("/me") });
  const config = useQuery({ queryKey: ["config"], queryFn: () => api.get<ConfigView>("/config") });
  const setup = useSetup();
  const [restarting, setRestarting] = useState<string | null>(null);

  const check = useMutation({
    mutationFn: () => api.post<{ status: string; detail: string }>("/update/check"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["health"] }),
  });
  const apply = useMutation({
    mutationFn: () => api.post<{ status: string; detail: string }>("/update/apply"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["health"] }),
  });
  const rollback = useMutation({
    mutationFn: () => api.post<{ version: string; detail: string }>("/update/rollback"),
    onSuccess: (res) => {
      setRestarting(res.detail);
      void reloadWhenBack();
    },
  });
  const mode = useMutation({
    mutationFn: (value: UpdateMode) => api.put<ConfigView>("/config", { updates_mode: value }),
    onSuccess: (data) => {
      qc.setQueryData(["config"], data);
      qc.invalidateQueries({ queryKey: ["health"] });
    },
  });

  const update = health.data?.update;
  const managed = update?.managed ?? false;
  const local = setup.data?.local ?? false;
  const current = config.data?.updates_mode ?? (update?.mode as UpdateMode | undefined);
  const pending = update?.available ?? update?.staged;

  return (
    <Card>
      <SectionTitle>Sistema</SectionTitle>
      <dl className="grid grid-cols-2 gap-y-2 text-sm">
        <dt className="text-muted">Versión</dt>
        <dd className="min-w-0 break-all tabular-nums">{health.data?.version ?? "—"}</dd>
        <dt className="text-muted">Conectado como</dt>
        <dd>
          {me.data ? (me.data.login ?? "este PC") : "—"}{" "}
          {me.data && <Badge>{me.data.via === "local" ? "local" : "tailnet"}</Badge>}
        </dd>
        <dt className="text-muted">Trabajos activos</dt>
        <dd>{health.data?.active_jobs ?? "—"}</dd>
      </dl>

      {pending && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl bg-accent/10 p-3 text-sm text-accent">
          <span className="flex-1">Nueva versión disponible: {pending}</span>
          {managed && current !== "auto" && (
            <Button disabled={apply.isPending} onClick={() => apply.mutate()}>
              Instalar ahora
            </Button>
          )}
        </div>
      )}

      {managed && (
        <div className="mt-4 grid gap-2">
          <span className="text-sm text-muted">Actualizaciones</span>
          <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Actualizaciones">
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                role="radio"
                aria-checked={current === m.value}
                disabled={!local || mode.isPending}
                title={local ? m.hint : LOCAL_ONLY_NOTE}
                onClick={() => mode.mutate(m.value)}
                className={cx(
                  "min-h-11 rounded-xl border px-2 text-sm transition disabled:cursor-not-allowed",
                  current === m.value ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:text-fg",
                )}
              >
                {m.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted">{MODES.find((m) => m.value === current)?.hint}</p>
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="secondary" disabled={check.isPending} onClick={() => check.mutate()}>
          {check.isPending ? "Buscando…" : "Buscar actualización"}
        </Button>
        {managed && (
          <Button
            variant="ghost"
            disabled={rollback.isPending || restarting !== null}
            onClick={() => {
              void confirm({
                title: "¿Volver a la versión anterior?",
                body: "Oraqlo se reiniciará en unos segundos con la versión que tenías antes.",
                confirm: "Volver",
              }).then((ok) => ok && rollback.mutate());
            }}
          >
            Volver a la versión anterior
          </Button>
        )}
      </div>
      {(check.data ?? apply.data) && <p className="mt-2 text-sm text-muted">{(apply.data ?? check.data)?.detail}</p>}
      {restarting && <p className="mt-2 text-sm text-accent">{restarting}</p>}
      <ErrorNote error={check.error ?? apply.error ?? rollback.error ?? mode.error} />
    </Card>
  );
}

export function DoctorCard() {
  const doctor = useMutation({ mutationFn: () => api.get<{ ok: boolean; checks: Check[] }>("/doctor") });
  return (
    <Card>
      <SectionTitle>Diagnóstico</SectionTitle>
      <p className="mb-3 text-sm text-muted">Comprueba la base de datos y que los modelos que usas respondan de verdad.</p>
      <Button variant="secondary" disabled={doctor.isPending} onClick={() => doctor.mutate()}>
        {doctor.isPending ? "Revisando…" : "Revisar que todo funcione"}
      </Button>
      {doctor.data && (
        <ul className="mt-3 space-y-2 text-sm">
          {doctor.data.checks.map((c) => (
            <li key={c.name} className="flex gap-2">
              <span className={c.ok ? "text-good" : c.optional ? "text-warn" : "text-bad"}>
                {c.ok ? "✓" : c.optional ? "–" : "✗"}
              </span>
              <span>
                <span className="font-medium">{c.name}:</span> {c.detail}
                {c.fix && <span className="block text-muted">{c.fix}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      <ErrorNote error={doctor.error} />
    </Card>
  );
}

export function ImportCard({ compact = false }: { compact?: boolean }) {
  const qc = useQueryClient();
  const setup = useSetup();
  const input = useRef<HTMLInputElement>(null);
  const imp = useMutation({
    mutationFn: async (files: FileList) => {
      const payload: Record<string, string> = {};
      for (const f of Array.from(files)) payload[f.name] = await f.text();
      return api.post<{ open_forecasts: number; resolved_forecasts: number; goals: number }>("/import-legacy", {
        files: payload,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries();
    },
  });
  const local = setup.data?.local ?? false;
  const body = (
    <>
      <p className="mb-3 text-sm text-muted">
        ¿Usabas la versión anterior de Oraqlo (la de terminal)? Elige sus archivos <code>oraqlo_ledger.json</code>,{" "}
        <code>oraqlo_goals.json</code> y <code>oraqlo_settings.json</code> para conservar tu historial y tus objetivos.
        Importar dos veces no duplica nada.
      </p>
      <input
        ref={input}
        type="file"
        accept=".json,application/json"
        multiple
        className="hidden"
        onChange={(e) => e.target.files?.length && imp.mutate(e.target.files)}
      />
      <Button variant="secondary" disabled={!local || imp.isPending} title={local ? undefined : LOCAL_ONLY_NOTE} onClick={() => input.current?.click()}>
        {imp.isPending ? "Importando…" : "Elegir archivos…"}
      </Button>
      {imp.data && (
        <p className="mt-2 text-sm text-good">
          ✓ Importadas {imp.data.open_forecasts} predicciones abiertas, {imp.data.resolved_forecasts} resueltas y {imp.data.goals} objetivos.
        </p>
      )}
      <ErrorNote error={imp.error} />
    </>
  );
  if (compact) return body;
  return (
    <Card>
      <SectionTitle>Importar historial antiguo</SectionTitle>
      {body}
    </Card>
  );
}

export function AllowedUsers() {
  const qc = useQueryClient();
  const config = useQuery({ queryKey: ["config"], queryFn: () => api.get<ConfigView>("/config") });
  const setup = useSetup();
  const [text, setText] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (users: string[]) => api.put<ConfigView>("/config", { allowed_users: users }),
    onSuccess: (data) => {
      qc.setQueryData(["config"], data);
      setText(null);
    },
  });
  const value = text ?? config.data?.allowed_users.join(", ") ?? "";
  const local = setup.data?.local ?? false;
  return (
    <details className="mt-4 text-sm">
      <summary className="cursor-pointer text-muted">Quién puede entrar desde la tailnet</summary>
      <p className="mt-2 text-xs text-muted">
        Vacío = cualquier dispositivo de tu tailnet. Para limitarlo, escribe los correos de Tailscale permitidos separados
        por comas.
      </p>
      <form
        className="mt-2 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(value.split(/[,\s]+/).filter(Boolean));
        }}
      >
        <input
          className={cx(inputClass, "min-w-0 flex-1")}
          value={value}
          disabled={!local}
          placeholder="tu@correo.com"
          onChange={(e) => setText(e.target.value)}
          aria-label="Usuarios permitidos"
        />
        <Button type="submit" variant="secondary" disabled={!local || text === null || save.isPending}>
          Guardar
        </Button>
      </form>
      <ErrorNote error={save.error} />
    </details>
  );
}
