import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import ConnectionCard from "../components/ConnectionCard";
import ProvidersCard from "../components/ProvidersCard";
import RolesCard from "../components/RolesCard";
import { DoctorCard, ImportCard, UpdatesCard } from "../components/SystemCards";
import { Button, Card, ErrorNote, SectionTitle } from "../components/ui";
import { api } from "../lib/api";
import { pushSupported, subscribePush, unsubscribePush } from "../lib/push";

function NotificationsCard() {
  const [pushState, setPushState] = useState<string | null>(null);
  const push = useMutation({
    mutationFn: async (on: boolean) => (on ? subscribePush() : unsubscribePush()),
    onSuccess: (msg) => setPushState(msg),
  });
  const test = useMutation({ mutationFn: () => api.post("/push/test") });
  return (
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
