// Abrir una página de la app: primero el servidor, y la copia guardada si no responde.
//
// Antes se servía siempre el index.html del precache. Pero ese HTML lleva la versión
// del servidor (meta oraqlo-version) y solo se renovaba si cambiaba la interfaz: tras
// una actualización solo del backend, la página seguía diciendo la versión vieja y el
// aviso «Oraqlo se actualizó» no se iba nunca. El servidor es local o de la tailnet y
// responde en milisegundos; sin red (o lento) se abre igual, desde la copia.

export const NAVIGATION_TIMEOUT_MS = 2000;

function usable(res: Response): boolean {
  return res.ok && (res.headers.get("content-type") ?? "").includes("text/html");
}

export async function networkFirst(
  fromNetwork: () => Promise<Response>,
  fromCache: () => Promise<Response>,
  timeoutMs = NAVIGATION_TIMEOUT_MS,
): Promise<Response> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), timeoutMs);
  });
  try {
    const res = await Promise.race([fromNetwork().catch(() => null), timeout]);
    if (res && usable(res)) return res;
  } finally {
    clearTimeout(timer);
  }
  return fromCache();
}
