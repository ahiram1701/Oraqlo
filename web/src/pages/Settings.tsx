import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Card, ErrorNote, SectionTitle } from "../components/ui";
import { api, type Health, type ProvidersView } from "../lib/api";
import { pushSupported, subscribePush, unsubscribePush } from "../lib/push";

export default function Settings() {
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<{ login: string | null; via: string }>("/me") });
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api.get<ProvidersView>("/providers") });
  const [pushState, setPushState] = useState<string | null>(null);

  const push = useMutation({
    mutationFn: async (on: boolean) => (on ? subscribePush() : unsubscribePush()),
    onSuccess: (msg) => setPushState(msg),
  });
  const test = useMutation({ mutationFn: () => api.post("/push/test") });
  const update = useMutation({ mutationFn: () => api.post<{ status: string; detail: string }>("/update/check") });

  const used = new Set(providers.data?.roles.map((r) => r.provider));

  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle>Sistema</SectionTitle>
        <dl className="grid grid-cols-2 gap-y-2 text-sm">
          <dt className="text-muted">Versión</dt>
          <dd className="min-w-0 break-all tabular-nums">{health.data?.version ?? "—"}</dd>
          <dt className="text-muted">Conectado como</dt>
          <dd>{me.data ? (me.data.login ?? "este equipo") : "—"} {me.data && <Badge>{me.data.via}</Badge>}</dd>
          <dt className="text-muted">Trabajos activos</dt>
          <dd>{health.data?.active_jobs ?? "—"}</dd>
        </dl>
        {health.data?.update?.available && (
          <p className="mt-3 rounded-xl bg-accent/10 p-3 text-sm text-accent">
            Nueva versión disponible: {health.data.update.available}
          </p>
        )}
        {health.data?.update !== undefined && (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="secondary" disabled={update.isPending} onClick={() => update.mutate()}>
              Buscar actualización
            </Button>
          </div>
        )}
        {update.data && <p className="mt-2 text-sm text-muted">{update.data.detail}</p>}
        <ErrorNote error={update.error} />
      </Card>

      <Card>
        <SectionTitle>Notificaciones</SectionTitle>
        <p className="mb-3 text-sm text-muted">
          Avisos en este dispositivo cuando termina un análisis, vence un hito o Oraqlo se actualiza. En iPhone, primero
          añade la app a la pantalla de inicio.
        </p>
        {pushSupported() ? (
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => push.mutate(true)} disabled={push.isPending}>
              Activar en este dispositivo
            </Button>
            <Button variant="secondary" onClick={() => test.mutate()}>
              Enviar prueba
            </Button>
            <Button variant="ghost" onClick={() => push.mutate(false)}>
              Desactivar
            </Button>
          </div>
        ) : (
          <p className="text-sm text-warn">Este navegador no admite notificaciones push (o la app no está instalada).</p>
        )}
        {pushState && <p className="mt-2 text-sm text-muted">{pushState}</p>}
        <ErrorNote error={push.error ?? test.error} />
      </Card>

      <Card>
        <SectionTitle>Roles del panel</SectionTitle>
        <ul className="divide-y divide-line text-sm">
          {providers.data?.roles.map((r) => (
            <li key={r.name} className="flex items-center justify-between gap-2 py-2">
              <span className="font-medium">{r.name}</span>
              <span className="truncate text-muted">
                {r.provider}:{r.model} {r.effort ? `· effort ${r.effort}` : `· T ${r.temperature}`}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-muted">
          Se configuran en <code>oraqlo.toml</code> (ejecuta <code>oraqlo config</code> para ver la ruta).
        </p>
      </Card>

      <Card>
        <SectionTitle>Proveedores</SectionTitle>
        <ul className="divide-y divide-line text-sm">
          {providers.data?.providers
            .filter((p) => used.has(p.name) || p.key_present)
            .map((p) => (
              <li key={p.name} className="flex items-center justify-between py-2">
                <span>{p.name}</span>
                {p.key_present === null ? (
                  <Badge>sin clave</Badge>
                ) : p.key_present ? (
                  <Badge tone="good">clave presente</Badge>
                ) : (
                  <Badge tone="bad">falta {p.api_key_env}</Badge>
                )}
              </li>
            ))}
        </ul>
        <ErrorNote error={providers.error} />
      </Card>
    </div>
  );
}
