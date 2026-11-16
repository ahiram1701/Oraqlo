/// <reference lib="webworker" />
// Service worker de Oraqlo: abre la app (del servidor, o de la copia si no hay red) y recibe avisos push.
import { clientsClaim } from "workbox-core";
import { addRoute, cleanupOutdatedCaches, createHandlerBoundToURL, precache } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { networkFirst } from "./lib/navigationStrategy";
import { checkOrigin } from "./lib/swGuard";

declare const self: ServiceWorkerGlobalScope;

self.skipWaiting();
clientsClaim();
cleanupOutdatedCaches();
// Guardar la copia, pero sin registrar todavía su ruta: workbox resuelve «/» a
// index.html, y si esa ruta va primero toda apertura de la app sale de la copia
// (con la versión vieja). Las rutas se registran en orden y gana la primera.
precache(self.__WB_MANIFEST);

/** Otra app ocupa esta dirección: borrarse y recargar las pestañas desde la red. */
async function retire(): Promise<void> {
  await self.registration.unregister();
  for (const key of await caches.keys()) await caches.delete(key);
  const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of windows) {
    if ("navigate" in client) await (client as WindowClient).navigate(client.url);
  }
}

let checking: Promise<void> | null = null;
function verifyOrigin(): Promise<void> {
  // Una comprobación a la vez; sin red ("no-se") no se toca nada.
  checking ??= checkOrigin()
    .then((verdict) => (verdict === "otra-app" ? retire() : undefined))
    .finally(() => {
      checking = null;
    });
  return checking;
}

// Navegación SPA: cualquier ruta que no sea de la API pide index.html al servidor
// (así su versión siempre coincide) y, sin red, sirve la copia del precache. En
// paralelo comprueba que en esta dirección siga Oraqlo.
const shell = createHandlerBoundToURL("/index.html");
registerRoute(
  new NavigationRoute(
    (options) => {
      options.event?.waitUntil(verifyOrigin());
      return networkFirst(
        () => fetch(options.request, { cache: "no-store" }),
        () => shell(options),
      );
    },
    { denylist: [/^\/api\//] },
  ),
);
// Después de la navegación: JS, CSS, iconos y fuentes (con hash) salen de la copia.
addRoute();

self.addEventListener("activate", (event) => event.waitUntil(verifyOrigin()));

self.addEventListener("push", (event) => {
  let data: { title?: string; body?: string; url?: string; tag?: string } = {};
  try {
    data = event.data?.json() ?? {};
  } catch {
    data = { body: event.data?.text() };
  }
  const options: NotificationOptions = {
    body: data.body ?? "",
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    data: { url: data.url ?? "/" },
  };
  if (data.tag) options.tag = data.tag; // un tag vacío haría que unos avisos taparan a otros
  event.waitUntil(self.registration.showNotification(data.title ?? "Oraqlo", options));
});

// Al tocar un aviso: si la app ya está abierta, se le pide que navegue dentro de
// la SPA (sin recargar); si no, se abre en esa pantalla.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data?.url as string) ?? "/";
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const client = windows.find((c) => c.visibilityState === "visible") ?? windows[0];
      if (client) {
        client.postMessage({ type: "navigate", url });
        try {
          await client.focus();
          return;
        } catch {
          /* sin permiso para enfocar: se abre una ventana */
        }
      }
      await self.clients.openWindow(url);
    })().catch(() => self.clients.openWindow(url)),
  );
});

// El navegador puede renovar la suscripción por su cuenta: se rehace con la clave
// del servidor y se registra, para no dejar de recibir avisos sin saberlo.
self.addEventListener("pushsubscriptionchange", (event) => {
  const change = event as Event & { waitUntil(p: Promise<unknown>): void };
  change.waitUntil(
    (async () => {
      const res = await fetch("/api/push/key");
      const { public_key } = (await res.json()) as { public_key: string };
      const padding = "=".repeat((4 - (public_key.length % 4)) % 4);
      const raw = atob((public_key + padding).replace(/-/g, "+").replace(/_/g, "/"));
      const key = Uint8Array.from(raw, (c) => c.charCodeAt(0));
      const sub = await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      await fetch("/api/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sub.toJSON()),
      });
    })().catch(() => undefined),
  );
});
