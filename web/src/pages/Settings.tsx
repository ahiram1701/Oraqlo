import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import ConnectionCard from "../components/ConnectionCard";
import ProvidersCard from "../components/ProvidersCard";
import RolesCard from "../components/RolesCard";
import WebSearchCard from "../components/WebSearchCard";
import { DoctorCard, ImportCard, UpdatesCard } from "../components/SystemCards";
import { useSetup } from "../lib/setup";
import { Badge, Button, Card, ErrorNote, Notice, SectionTitle, Toggle, cx } from "../components/ui";
import { api } from "../lib/api";
import { pushState, subscribePush, unsubscribePush } from "../lib/push";
import { getTheme, setTheme, type ThemeChoice } from "../lib/theme";
import { Icon } from "../components/icons";

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
        <Notice tone="warn">{s.reason}</Notice>
      ) : s.kind === "denied" ? (
        <Notice tone="warn">
          Bloqueaste las notificaciones de Oraqlo. Permítelas en los ajustes del sistema (en iPhone: Ajustes → Notificaciones →
          Oraqlo) y vuelve aquí.
        </Notice>
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

function ThemeCard() {
  const [choice, setChoice] = useState<ThemeChoice>(getTheme);
  const options: { value: ThemeChoice; label: string; icon: "device" | "sun" | "moon" }[] = [
    { value: "system", label: "Como el sistema", icon: "device" },
    { value: "light", label: "Claro", icon: "sun" },
    { value: "dark", label: "Oscuro", icon: "moon" },
  ];
  return (
    <Card>
      <SectionTitle>Apariencia</SectionTitle>
      <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Tema">
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={choice === o.value}
            onClick={() => {
              setChoice(o.value);
              setTheme(o.value);
            }}
            className={cx(
              "flex min-h-16 flex-col items-center justify-center gap-1 rounded-xl border text-xs font-medium transition",
              choice === o.value ? "border-accent bg-accent/10 text-fg" : "border-line text-muted hover:text-fg",
            )}
          >
            <Icon name={o.icon} />
            {o.label}
          </button>
        ))}
      </div>
    </Card>
  );
}

const GROUPS = [
  { id: "ia", label: "IA" },
  { id: "dispositivos", label: "Dispositivos" },
  { id: "sistema", label: "Sistema" },
];

function Group({ id, title, hint, children }: { id: string; title: string; hint: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-4 space-y-4">
      <div className="px-1 pt-2">
        <h2 id={`${id}-title`} className="text-base font-semibold">
          {title}
        </h2>
        <p className="text-sm text-muted">{hint}</p>
      </div>
      {children}
    </section>
  );
}

export default function Settings() {
  // Enlaces como /ajustes#busqueda-web (desde Preguntar o un aviso) llevan a la tarjeta.
  const { hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [hash]);
  return (
    <div className="space-y-6">
      <nav aria-label="Secciones de ajustes" className="no-scrollbar -mx-1 flex gap-2 overflow-x-auto px-1">
        {GROUPS.map((g) => (
          <a
            key={g.id}
            href={`#${g.id}`}
            onClick={(e) => {
              e.preventDefault();
              document.getElementById(g.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}
            className="inline-flex min-h-9 shrink-0 items-center rounded-full border border-line px-4 text-sm text-muted hover:border-accent hover:text-fg"
          >
            {g.label}
          </a>
        ))}
      </nav>
      <Group id="ia" title="IA" hint="Qué modelo usa Oraqlo y con qué claves.">
        <ProvidersCard />
        <WebSearchCard />
        <RolesCard />
      </Group>
      <Group id="dispositivos" title="Dispositivos" hint="Celular, avisos y apariencia.">
        <ConnectionCard />
        <NotificationsCard />
        <ThemeCard />
      </Group>
      <Group id="sistema" title="Sistema" hint="Versión, actualizaciones, diagnóstico y datos.">
        <UpdatesCard />
        <DoctorCard />
        <ImportCard />
      </Group>
    </div>
  );
}
