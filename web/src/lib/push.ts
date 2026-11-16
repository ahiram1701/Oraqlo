import { api } from "./api";

export function pushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

/** iPhone/iPad: Web Push solo funciona con la app instalada en la pantalla de inicio. */
export function isIOS(): boolean {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

export function isStandalone(): boolean {
  return (
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

export function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function sameKey(a: ArrayBuffer | null | undefined, b: Uint8Array): boolean {
  if (!a) return false;
  const x = new Uint8Array(a);
  return x.length === b.length && x.every((v, i) => v === b[i]);
}

/**
 * Suscribe este dispositivo. Si ya había una suscripción hecha con OTRA clave del
 * servidor (base restaurada, reinstalación), se da de baja y se rehace: si no, el
 * servicio de push rechazaría cada aviso en silencio.
 */
export async function subscribePush(): Promise<string> {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return "Permiso de notificaciones denegado. Actívalo en los ajustes del sistema.";
  const reg = await navigator.serviceWorker.ready;
  const { public_key } = await api.get<{ public_key: string }>("/push/key");
  const key = urlBase64ToUint8Array(public_key);
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub.options.applicationServerKey, key)) {
    await sub.unsubscribe();
    sub = null;
  }
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  await api.post("/push/subscribe", sub.toJSON());
  return "Notificaciones activadas en este dispositivo.";
}

export async function unsubscribePush(): Promise<string> {
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (sub) {
    await api.post("/push/unsubscribe", { endpoint: sub.endpoint });
    await sub.unsubscribe();
  }
  return "Notificaciones desactivadas.";
}

export type PushState =
  | { kind: "unsupported"; reason: string }
  | { kind: "denied" }
  | { kind: "off" }
  | { kind: "on" };

/** Estado real de ESTE dispositivo (no solo si el navegador lo soporta). */
export async function pushState(): Promise<PushState> {
  if (isIOS() && !isStandalone())
    return { kind: "unsupported", reason: "En iPhone, primero añade Oraqlo a la pantalla de inicio (Compartir → Añadir a inicio) y ábrelo desde ahí." };
  if (!pushSupported()) return { kind: "unsupported", reason: "Este navegador no admite notificaciones push." };
  if (Notification.permission === "denied") return { kind: "denied" };
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  if (!sub) return { kind: "off" };
  const status = await api.get<{ subscribed: boolean }>(`/push/status?endpoint=${encodeURIComponent(sub.endpoint)}`);
  return status.subscribed ? { kind: "on" } : { kind: "off" };
}
