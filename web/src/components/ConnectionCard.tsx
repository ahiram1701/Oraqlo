import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type PortChangeResult, type ServerInfo } from "../lib/api";
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

export default function ConnectionCard() {
  const server = useQuery({ queryKey: ["server"], queryFn: () => api.get<ServerInfo>("/server") });
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
      else setWaiting(`Oraqlo no respondió en el puerto ${res.port}. Revisa el registro (app/update.log).`);
    },
  });

  const info = server.data;
  const value = Number(port);
  const valid = Number.isInteger(value) && value >= 1024 && value <= 65535 && value !== info?.port;
  const ts = info?.tailscale;

  return (
    <Card>
      <SectionTitle>Conexión</SectionTitle>
      <dl className="grid grid-cols-2 gap-y-2 text-sm">
        <dt className="text-muted">En este PC</dt>
        <dd className="min-w-0 break-all">{info?.local_url ?? "—"}</dd>
        <dt className="text-muted">En el celular</dt>
        <dd className="min-w-0 break-all">
          {ts?.url && ts.enabled ? (
            <>
              {ts.url}{" "}
              {ts.serving_port === info?.port ? <Badge tone="good">publicada</Badge> : <Badge tone="warn">sincronizando</Badge>}
            </>
          ) : ts?.installed ? (
            <span className="text-muted">Tailscale no publica Oraqlo (vuelve a ejecutar el instalador)</span>
          ) : (
            <span className="text-muted">Instala Tailscale para usarla desde el celular</span>
          )}
        </dd>
      </dl>

      {info?.managed ? (
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
        info && (
          <p className="mt-3 text-xs text-muted">
            Copia de desarrollo: el puerto se elige con <code>oraqlo serve --port N</code>.
          </p>
        )
      )}
      <p className="mt-3 text-xs text-muted">
        Si otra aplicación ocupa el puerto al arrancar, Oraqlo usa el siguiente libre por sí solo y mantiene la misma
        dirección en el celular.
      </p>
      {waiting && <p className="mt-2 text-sm text-accent">{waiting}</p>}
      <ErrorNote error={change.error ?? server.error} />
    </Card>
  );
}
