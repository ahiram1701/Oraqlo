import { vi } from "vitest";
import { subscribePush, urlBase64ToUint8Array } from "./push";

const SERVER_KEY = "BAECAwQ"; // base64url de [4, 1, 2, 3, 4]

function setup(existingKey: number[] | null) {
  const posted: unknown[] = [];
  const unsubscribe = vi.fn(async () => true);
  const fresh = { endpoint: "https://push/nuevo", toJSON: () => ({ endpoint: "https://push/nuevo" }) };
  const existing = existingKey && {
    endpoint: "https://push/viejo",
    options: { applicationServerKey: new Uint8Array(existingKey).buffer },
    unsubscribe,
    toJSON: () => ({ endpoint: "https://push/viejo" }),
  };
  const subscribe = vi.fn(async () => fresh);
  vi.stubGlobal("Notification", { requestPermission: async () => "granted", permission: "granted" });
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { ready: Promise.resolve({ pushManager: { getSubscription: async () => existing, subscribe } }) },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith("/push/key")) return Response.json({ public_key: SERVER_KEY });
      posted.push(JSON.parse(String(init?.body)));
      return Response.json({ devices: 1 }, { status: 201 });
    }),
  );
  return { posted, unsubscribe, subscribe };
}

afterEach(() => vi.unstubAllGlobals());

test("la clave del servidor se decodifica de base64url", () => {
  expect(Array.from(urlBase64ToUint8Array(SERVER_KEY))).toEqual([4, 1, 2, 3, 4]);
});

test("si la suscripción usa otra clave del servidor, se rehace", async () => {
  const { posted, unsubscribe, subscribe } = setup([9, 9, 9]);
  await subscribePush();
  expect(unsubscribe).toHaveBeenCalled();
  expect(subscribe).toHaveBeenCalled();
  expect(posted).toEqual([{ endpoint: "https://push/nuevo" }]);
});

test("si la clave coincide, se reutiliza la suscripción", async () => {
  const { posted, unsubscribe, subscribe } = setup([4, 1, 2, 3, 4]);
  await subscribePush();
  expect(unsubscribe).not.toHaveBeenCalled();
  expect(subscribe).not.toHaveBeenCalled();
  expect(posted).toEqual([{ endpoint: "https://push/viejo" }]);
});
