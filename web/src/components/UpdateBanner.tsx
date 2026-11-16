import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type Health } from "../lib/api";
import { bundleVersion, reloadToLatest, shouldAutoReload } from "../lib/appVersion";
import { Button } from "./ui";

const CHECK_MS = 5 * 60_000;

/** El servidor ya es otra versión que esta interfaz: recarga o lo ofrece. */
export default function UpdateBanner() {
  const mine = bundleVersion();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<Health>("/health"),
    refetchInterval: CHECK_MS,
    enabled: mine != null,
  });
  const served = health.data?.version;
  const stale = mine != null && !!served && served !== mine;
  const [reloading, setReloading] = useState(false);

  useEffect(() => {
    if (!stale || !served) return;
    const tryAuto = () => {
      if (shouldAutoReload(served)) {
        setReloading(true);
        void reloadToLatest(served);
      }
    };
    tryAuto();
    document.addEventListener("visibilitychange", tryAuto);
    return () => document.removeEventListener("visibilitychange", tryAuto);
  }, [stale, served]);

  if (!stale || !served) return null;
  return (
    <div
      role="status"
      className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-accent/40 bg-accent/10 px-4 py-3 text-sm"
    >
      <span>
        Oraqlo se actualizó a la <b>{served}</b>. Recarga para ver lo nuevo.
      </span>
      <Button
        disabled={reloading}
        onClick={() => {
          setReloading(true);
          void reloadToLatest(served);
        }}
      >
        {reloading ? "Recargando…" : "Recargar"}
      </Button>
    </div>
  );
}
