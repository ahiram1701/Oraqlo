// ¿Sigue siendo Oraqlo quien responde en esta dirección?
//
// El service worker sirve la app desde su caché sin preguntar al servidor. Si otra
// app pasa a ocupar la dirección (p. ej. Oraqlo se movió a otro puerto de la
// tailnet), el navegador nunca lo actualiza: la otra app no sirve /sw.js. Así que
// el propio service worker comprueba /api/health y, si contesta otra cosa, se borra.

export type Verdict = "oraqlo" | "otra-app" | "no-se";

/**
 * Decide a partir de la respuesta de /api/health:
 * - 200 con `app: "oraqlo"` → sigue siendo Oraqlo.
 * - 200 con otra cosa, o 404 → otra app ocupa la dirección (Oraqlo siempre tiene /api/health).
 * - 401/403 (Oraqlo rechazando a este usuario), 5xx o sin red → no se sabe: no tocar nada.
 */
export function judgeHealth(status: number, contentType: string | null, body: unknown): Verdict {
  if (status === 404) return "otra-app";
  if (status !== 200) return "no-se";
  if (!(contentType ?? "").includes("json")) return "otra-app";
  const app = typeof body === "object" && body !== null ? (body as { app?: unknown }).app : undefined;
  return app === "oraqlo" ? "oraqlo" : "otra-app";
}

/** Consulta /api/health sin caché y devuelve el veredicto ("no-se" si falla la red). */
export async function checkOrigin(fetcher: typeof fetch = fetch): Promise<Verdict> {
  try {
    const res = await fetcher("/api/health", { cache: "no-store" });
    const contentType = res.headers.get("content-type");
    let body: unknown = null;
    if ((contentType ?? "").includes("json")) {
      try {
        body = await res.json();
      } catch {
        body = null;
      }
    }
    return judgeHealth(res.status, contentType, body);
  } catch {
    return "no-se";
  }
}
