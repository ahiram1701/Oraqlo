// La interfaz guardada (service worker) puede ser de una versión anterior a la del
// servidor: tras una actualización automática, una pestaña o la ventana abierta
// seguirían con la interfaz vieja indefinidamente. Aquí se detecta y se corrige.

let registration: ServiceWorkerRegistration | undefined;

export function setRegistration(reg: ServiceWorkerRegistration): void {
  registration = reg;
}

/** Versión con la que se sirvió esta página (la inyecta el servidor en index.html). */
export function bundleVersion(): string | null {
  return document.querySelector<HTMLMetaElement>('meta[name="oraqlo-version"]')?.content || null;
}

const GUARD = "oraqlo-reload-for";
const FRESH_MS = 15_000;
const loadedAt = Date.now();

/** Recargar sola solo si nadie está escribiendo: recién abierta o en segundo plano. */
export function shouldAutoReload(target: string, now = Date.now(), hidden = document.visibilityState === "hidden"): boolean {
  let tried: string | null = null;
  try {
    tried = sessionStorage.getItem(GUARD);
  } catch {
    // sin sessionStorage: se recarga igual, a lo sumo una vez por carga
  }
  if (tried === target) return false; // ya se intentó: no entrar en bucle
  return hidden || now - loadedAt < FRESH_MS;
}

function controllerChange(timeoutMs: number): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      navigator.serviceWorker.removeEventListener("controllerchange", done);
      resolve();
    };
    const timer = setTimeout(done, timeoutMs);
    navigator.serviceWorker.addEventListener("controllerchange", done);
  });
}

/** Trae el service worker nuevo (con la interfaz nueva) y recarga. */
export async function reloadToLatest(target: string): Promise<void> {
  try {
    sessionStorage.setItem(GUARD, target);
  } catch {
    // sin sessionStorage
  }
  if (registration) {
    try {
      await registration.update();
      if (registration.installing || registration.waiting) await controllerChange(10_000);
    } catch {
      // sin red: la recarga normal ya lo intentará
    }
  }
  location.reload();
}
