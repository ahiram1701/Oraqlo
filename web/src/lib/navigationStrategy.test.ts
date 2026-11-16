import { vi } from "vitest";
import { networkFirst } from "./navigationStrategy";

const html = (body: string, status = 200) =>
  new Response(body, { status, headers: { "Content-Type": "text/html; charset=utf-8" } });
const cached = () => Promise.resolve(html("copia"));

describe("abrir la app: servidor primero, copia si no responde", () => {
  it("con el servidor respondiendo, usa su HTML (con la versión al día)", async () => {
    const res = await networkFirst(() => Promise.resolve(html("nuevo")), cached);
    expect(await res.text()).toBe("nuevo");
  });

  it("sin red, abre la copia guardada", async () => {
    const res = await networkFirst(() => Promise.reject(new TypeError("offline")), cached);
    expect(await res.text()).toBe("copia");
  });

  it("si el servidor tarda, no hace esperar: abre la copia", async () => {
    vi.useFakeTimers();
    const pending = networkFirst(() => new Promise<Response>(() => undefined), cached, 2000);
    await vi.advanceTimersByTimeAsync(2001);
    expect(await (await pending).text()).toBe("copia");
    vi.useRealTimers();
  });

  it("un error o algo que no es HTML (p. ej. 401 de la tailnet) tampoco vale", async () => {
    expect(await (await networkFirst(() => Promise.resolve(html("x", 502)), cached)).text()).toBe("copia");
    const json = new Response("{}", { headers: { "Content-Type": "application/json" } });
    expect(await (await networkFirst(() => Promise.resolve(json), cached)).text()).toBe("copia");
  });
});
