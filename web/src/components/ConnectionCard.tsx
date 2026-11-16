import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { api, type PortChangeResult, type ServerInfo } from "../lib/api";
import { LOCAL_ONLY_NOTE } from "./ProvidersCard";
import { AllowedUsers } from "./SystemCards";
import { Badge, Button, Card, ErrorNote, SectionTitle, cx, inputClass } from "./ui";

const isLocal = () => ["127.0.0.1", "localhost", "[::1]"].includes(window.location.hostname);

/** Espera a que Oraqlo responda en `origin` (otro puerto = otro origen: basta con que conecte). */
async function waitFor(origin: string, sameOrigin: boolean, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  await new Promise((r) => setTimeout(r, 2500)); // el servidor actual tarda un poco en salir
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${origin}/api/health`, sameOrigin ? { cache: "no-store" } : { mode: "no-cors" });
      if (!sameOrigin || res.ok) return true;
    } catch {
      /* aún reiniciando */
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

function Qr({ url }: { url: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    QRCode.toDataURL(url, { margin: 1, width: 200 }).then(setSrc, () => setSrc(null));
  }, [url]);
  return src ? <img src={src} alt={`Código QR de ${url}`} className="size-40 rounded-xl bg-white p-2" /> : null;
}

/** Celular: guía según el estado de Tailscale hasta dejar Oraqlo publicado. */
function PhoneAccess({ info, local }: { info: ServerInfo; local: boolean }) {
  const qc = useQueryClient();
  const ts = info.tailscale;
  const publish = useMutation({
    mutationFn: (enabled: boolean) => api.put<ServerInfo["tailscale"]>("/server/tailscale", { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["server"] }),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["server"] });

  if (ts.state === "missing")
    return (
      <div className="space-y-2 text-sm">
        <p className="text-muted">
          Para abrir Oraqlo en el celular, instala <strong>Tailscale</strong> (gratis) en este PC y en tu teléfono, con la
          misma cuenta.
        </p>
        <div className="flex flex-wrap gap-2">
          <a className="inline-flex min-h-11 items-center rounded-xl bg-accent px-4 font-medium text-white" href="https://tailscale.com/download" target="_blank" rel="noreferrer">
            Descargar Tailscale
          </a>
          <Button variant="secondary" onClick={refresh}>
            Ya lo instalé
          </Button>
        </div>
      </div>
    );
  if (ts.state !== "running")
    return (
      <div className="space-y-2 text-sm">
        <p className="text-muted">Abre Tailscale en este PC e inicia sesión. Esta tarjeta se actualiza sola.</p>
        <Button variant="secondary" onClick={refresh}>
          Comprobar de nuevo
        </Button>
      </div>
    );
  if (!ts.enabled || !ts.url)
    return (
      <div className="space-y-2 text-sm">
        <p className="text-muted">Tailscale está listo. Publica Oraqlo en tu tailnet (solo tus dispositivos podrán abrirlo).</p>
        <Button disabled={!local || publish.isPending} title={local ? undefined : LOCAL_ONLY_NOTE} onClick={() => publish.mutate(true)}>
          {publish.isPending ? "Publicando…" : "Publicar en el celular"}
        </Button>
        <ErrorNote error={publish.error} />
      </div>
    );
  return (
    <div className="flex flex-wrap items-start gap-4 text-sm">
      <Qr url={ts.url} />
      <div className="min-w-0 flex-1 space-y-2">
        <p className="break-all">
          <a className="text-accent underline" href={ts.url} target="_blank" rel="noreferrer">
            {ts.url}
          </a>{" "}
          {ts.serving_port === info.port ? <Badge tone="good">publicada</Badge> : <Badge tone="warn">sincronizando</Badge>}
        </p>
        <p className="text-muted">
          Escanea el código con el celular (con Tailscale activo) y elige «Añadir a pantalla de inicio» para instalarla.
        </p>
        {local && (
          <Button variant="ghost" className="px-0" disabled={publish.isPending} onClick={() => publish.mutate(false)}>
            Dejar de publicar
          </Button>
        )}
        <ErrorNote error={publish.error} />
      </div>
    </div>
  );
}

export default function ConnectionCard() {
  const server = useQuery({
    queryKey: ["server"],
    queryFn: () => api.get<ServerInfo>("/server"),
    // Mientras se espera a que el usuario abra Tailscale, se vuelve a mirar solo.
    refetchInterval: (q) => {
      const state = q.state.data?.tailscale.state;
      return state === "needs_login" || state === "stopped" ? 5000 : false;
    },
  });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<{ via: string }>("/me") });
  const local = me.data?.via === "local";
  const [port, setPort] = useState("");
  const [waiting, setWaiting] = useState<string | null>(null);

  const change = useMutation({
    mutationFn: (value: number) => api.put<PortChangeResult>("/server/port", { port: value }),
    onSuccess: async (res) => {
      if (!res.restarting) return;
      const local = isLocal();
      // En este PC la dirección cambia de puerto; por Tailscale la URL del celular es la misma.
      const target = local ? `${window.location.protocol}//${window.location.hostname}:${res.port}` : window.location.origin;
      setWaiting(`Reiniciando en el puerto ${res.port}…`);
      const ok = await waitFor(target, !local);
      if (ok) window.location.href = target + window.location.pathname;
      else setWaiting(`Oraqlo no respondió en el puerto ${res.port}. Ábrelo de nuevo desde el menú Inicio.`);
    },
  });

  const info = server.data;
  const value = Number(port);
  const valid = Number.isInteger(value) && value >= 1024 && value <= 65535 && value !== info?.port;

  return (
    <Card>
      <SectionTitle>Conexión</SectionTitle>
      <dl className="grid grid-cols-2 gap-y-2 text-sm">
        <dt className="text-muted">En este PC</dt>
        <dd className="min-w-0 break-all">{info?.local_url ?? "—"}</dd>
      </dl>
      {info?.managed && (
        <div className="mt-4">
          <h3 className="mb-2 text-sm font-medium">En el celular</h3>
          <PhoneAccess info={info} local={local} />
        </div>
      )}

      {info?.managed && local ? (
        <form
          className="mt-4 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid) change.mutate(value);
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted">Puerto local</span>
            <input
              className={cx(inputClass, "w-32 tabular-nums")}
              inputMode="numeric"
              placeholder={String(info.port ?? "")}
              value={port}
              onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))}
              aria-label="Puerto local"
            />
          </label>
          <Button type="submit" disabled={!valid || change.isPending || waiting !== null}>
            Cambiar y reiniciar
          </Button>
        </form>
      ) : (
        info &&
        !info.managed && <p className="mt-3 text-xs text-muted">Copia de desarrollo: sin publicación ni cambio de puerto.</p>
      )}
      <p className="mt-3 text-xs text-muted">
        Si otra aplicación ocupa el puerto al arrancar, Oraqlo usa el siguiente libre por sí solo y mantiene la misma
        dirección en el celular.
      </p>
      {waiting && <p className="mt-2 text-sm text-accent">{waiting}</p>}
      <ErrorNote error={change.error ?? server.error} />
      {info?.managed && <AllowedUsers />}
    </Card>
  );
}
