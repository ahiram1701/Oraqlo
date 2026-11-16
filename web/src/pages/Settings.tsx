import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import ConnectionCard from "../components/ConnectionCard";
import ProvidersCard from "../components/ProvidersCard";
import RolesCard from "../components/RolesCard";
import { DoctorCard, ImportCard, UpdatesCard } from "../components/SystemCards";
import { useSetup } from "../components/ProvidersCard";
import { Badge, Button, Card, ErrorNote, SectionTitle, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { pushState, subscribePush, unsubscribePush } from "../lib/push";

type NotifyWindows = { supported: boolean; enabled: boolean };

/** Toasts de Windows en este PC (los emite el servidor; solo desde el propio PC). */
export function WindowsNotifications() {
  const qc = useQueryClient();
  const setup = useSetup();
  const state = useQuery({ queryKey: ["notify-windows"], queryFn: () => api.get<NotifyWindows>("/notify/windows") });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.put<NotifyWindows>("/notify/windows", { enabled }),
    onSuccess: (data) => qc.setQueryData(["notify-windows"], data),
  });
  const test = useMutation({ mutationFn: () => api.post("/notify/windows/test") });
  if (!state.data?.supported || !setup.data?.local) return null;
  return (
    <div className="mb-4 border-b border-line pb-4">
      <div className="flex flex-wrap items-center gap-2">
        <Toggle checked={state.data.enabled} onChange={(v) => toggle.mutate(v)} label="Avisos de Windows en este PC" />
        {state.data.enabled && (
          <Button variant="ghost" disabled={test.isPending} onClick={() => test.mutate()}>
            Probar
          </Button>
        )}
      </div>
      <ErrorNote error={toggle.error ?? test.error} />
    </div>
  );
}

function NotificationsCard() {
  const qc = useQueryClient();
  const state = useQuery({ queryKey: ["push-state"], queryFn: pushState });
  const [note, setNote] = useState<string | null>(null);
  const push = useMutation({
    mutationFn: async (on: boolean) => (on ? subscribePush() : unsubscribePush()),
    onSuccess: (msg) => {
      setNote(msg);
      qc.invalidateQueries({ queryKey: ["push-state"] });
    },
  });
  const test = useMutation({
    mutationFn: () => api.post<{ delivered: number }>("/push/test"),
    onSuccess: () => setNote("Aviso de prueba enviado: debería llegarte en unos segundos."),
  });
  const s = state.data;
  return (
    <Card>
      <SectionTitle>Notificaciones</SectionTitle>
      <WindowsNotifications />
      <p className="mb-3 text-sm text-muted">
        Avisos en este dispositivo cuando termina un análisis, vence un hito, falla la práctica o Oraqlo se actualiza.
      </p>
      {!s ? null : s.kind === "unsupported" ? (
        <p className="rounded-xl bg-warn/10 p-3 text-sm text-warn">{s.reason}</p>
      ) : s.kind === "denied" ? (
        <p className="rounded-xl bg-warn/10 p-3 text-sm text-warn">
          Bloqueaste las notificaciones de Oraqlo. Permítelas en los ajustes del sistema (en iPhone: Ajustes → Notificaciones →
          Oraqlo) y vuelve aquí.
        </p>
      ) : (
        <>
          <p className="mb-3 flex items-center gap-2 text-sm">
            En este dispositivo:{" "}
            {s.kind === "on" ? <Badge tone="good">activadas</Badge> : <Badge>desactivadas</Badge>}
          </p>
          <div className="flex flex-wrap gap-2">
            {s.kind === "on" ? (
              <>
                <Button variant="secondary" disabled={test.isPending} onClick={() => test.mutate()}>
                  {test.isPending ? "Enviando…" : "Enviar prueba"}
                </Button>
                <Button variant="ghost" disabled={push.isPending} onClick={() => push.mutate(false)}>
                  Desactivar
                </Button>
              </>
            ) : (
              <Button onClick={() => push.mutate(true)} disabled={push.isPending}>
                {push.isPending ? "Activando…" : "Activar en este dispositivo"}
              </Button>
            )}
          </div>
        </>
      )}
      {note && <p className="mt-2 text-sm text-muted">{note}</p>}
      <ErrorNote error={push.error ?? test.error ?? state.error} />
    </Card>
  );
}

export default function Settings() {
  return (
    <div className="space-y-4">
      <ProvidersCard />
      <RolesCard />
      <ConnectionCard />
      <NotificationsCard />
      <UpdatesCard />
      <DoctorCard />
      <ImportCard />
    </div>
  );
}
