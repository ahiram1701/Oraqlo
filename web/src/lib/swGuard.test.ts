import { checkOrigin, judgeHealth } from "./swGuard";

const JSON_CT = "application/json";

test("sigue siendo Oraqlo solo si /api/health dice app=oraqlo", () => {
  expect(judgeHealth(200, JSON_CT, { app: "oraqlo", status: "ok" })).toBe("oraqlo");
  // Mamoney (lo que pasó de verdad): 200 con su propio health, sin app=oraqlo.
  expect(judgeHealth(200, JSON_CT, { status: "ok", version: "0.7.0" })).toBe("otra-app");
  expect(judgeHealth(200, "text/html; charset=utf-8", null)).toBe("otra-app");
  expect(judgeHealth(404, "text/html", null)).toBe("otra-app");
});

test("ante la duda no se borra nada", () => {
  expect(judgeHealth(401, JSON_CT, { detail: "no autorizado" })).toBe("no-se");
  expect(judgeHealth(403, JSON_CT, { detail: "funnel" })).toBe("no-se");
  expect(judgeHealth(502, "text/html", null)).toBe("no-se");
});

test("checkOrigin consulta sin caché y tolera la falta de red", async () => {
  const calls: RequestInit[] = [];
  const ok = (async (_url: string, init: RequestInit) => {
    calls.push(init);
    return new Response(JSON.stringify({ app: "oraqlo" }), { headers: { "Content-Type": JSON_CT } });
  }) as unknown as typeof fetch;
  expect(await checkOrigin(ok)).toBe("oraqlo");
  expect(calls[0].cache).toBe("no-store");

  const other = (async () =>
    new Response(JSON.stringify({ status: "ok" }), { headers: { "Content-Type": JSON_CT } })) as unknown as typeof fetch;
  expect(await checkOrigin(other)).toBe("otra-app");

  const offline = (async () => {
    throw new TypeError("Failed to fetch");
  }) as unknown as typeof fetch;
  expect(await checkOrigin(offline)).toBe("no-se");
});
