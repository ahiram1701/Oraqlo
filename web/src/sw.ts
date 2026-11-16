/// <reference lib="webworker" />
// Service worker de Oraqlo: abre la app al instante (precaché) y recibe avisos push.
import { clientsClaim } from "workbox-core";
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { checkOrigin } from "./lib/swGuard";

declare const self: ServiceWorkerGlobalScope;

self.skipWaiting();
clientsClaim();
cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST);

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

// Navegación SPA: cualquier ruta que no sea de la API sirve index.html cacheado
// (abre al instante) y, en paralelo, comprueba que en esta dirección siga Oraqlo.
const shell = createHandlerBoundToURL("/index.html");
registerRoute(
  new NavigationRoute(
    (options) => {
      options.event?.waitUntil(verifyOrigin());
      return shell(options);
    },
    { denylist: [/^\/api\//] },
  ),
);

self.addEventListener("activate", (event) => event.waitUntil(verifyOrigin()));

self.addEventListener("push", (event) => {
  let data: { title?: string; body?: string; url?: string; tag?: string } = {};
  try {
    data = event.data?.json() ?? {};
  } catch {
    data = { body: event.data?.text() };
  }
  event.waitUntil(
    self.registration.showNotification(data.title ?? "Oraqlo", {
      body: data.body ?? "",
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      tag: data.tag,
      data: { url: data.url ?? "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data?.url as string) ?? "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if ("focus" in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
