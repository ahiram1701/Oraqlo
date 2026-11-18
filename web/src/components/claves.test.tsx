import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { OptionsView } from "../lib/api";
import { MetaculusToken } from "./MetaculusToken";
import MyApisCard from "./MyApisCard";
import { FeedbackProvider } from "./ui";

type Handler = (method: string, body: any) => unknown;

function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: { method: string; path: string; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = String(url).replace(/^\/api/, "");
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, path, body });
      const route = routes[`${method} ${path}`] ?? routes[path];
      if (route === undefined) return new Response(JSON.stringify({ detail: "no mock" }), { status: 404 });
      const data = typeof route === "function" ? (route as Handler)(method, body) : route;
      return new Response(JSON.stringify(data ?? {}), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

function renderUi(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <FeedbackProvider>{ui}</FeedbackProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

const OPTIONS: OptionsView = {
  markets: true,
  decompose: true,
  reference: true,
  watch: true,
  watch_interval_hours: 24,
  watch_daily_budget: 5,
  watch_alert_points: 10,
  metaculus: false,
  metaculus_hint: null,
  web_search: true,
};

test("el token de Metaculus se pega en Ajustes y se prueba", async () => {
  const calls = mockApi({
    "PUT /keys/METACULUS_TOKEN": {},
    "POST /metaculus/test": { ok: true, detail: "Token válido: Metaculus ya cuenta como una voz más." },
  });
  renderUi(<MetaculusToken options={OPTIONS} local />);
  expect(screen.getByText("sin token")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Añadir token" }));
  expect(screen.getByRole("link", { name: /Consíguelo aquí/ })).toHaveAttribute("href", "https://www.metaculus.com/aib/");
  fireEvent.change(screen.getByLabelText("Token de Metaculus"), { target: { value: "tok-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
  await screen.findByText(/Token válido/);
  expect(calls.find((c) => c.method === "PUT")).toMatchObject({ path: "/keys/METACULUS_TOKEN", body: { value: "tok-1" } });
});

test("con token muestra la pista y no menciona archivos", () => {
  mockApi({});
  renderUi(<MetaculusToken options={{ ...OPTIONS, metaculus: true, metaculus_hint: "tok-…6789" }} local />);
  expect(screen.getByText("token tok-…6789")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Quitar" })).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/\.env|variable/);
});

test("Mis APIs: crear una, probarla y verla en la lista", async () => {
  let list: unknown[] = [];
  const calls = mockApi({
    "/setup": { ready: true, needs_setup: false, local: true, legacy_imported: false },
    "GET /providers/custom": () => list,
    "PUT /providers/custom/mi-servidor": (_m: string, body: any) => {
      list = [{ name: "mi-servidor", type: body.type, base_url: body.base_url, key_present: true, key_hint: "sk-…1234", used_by: [] }];
      return { providers: list, check: { name: "mi-servidor", ok: true, detail: "3 modelos disponibles.", fix: null } };
    },
  });
  renderUi(<MyApisCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Añadir una API" }));
  fireEvent.change(screen.getByPlaceholderText("mi-servidor"), { target: { value: "Mi-Servidor" } });
  fireEvent.change(screen.getByPlaceholderText("https://mi-servidor.com/v1"), { target: { value: "https://x.com/v1" } });
  fireEvent.change(screen.getByPlaceholderText("Opcional si no la pide"), { target: { value: "sk-1234" } });
  fireEvent.click(screen.getByRole("button", { name: "Guardar y probar" }));
  await screen.findByText(/Funciona\. 3 modelos disponibles/);
  expect(calls.find((c) => c.method === "PUT")).toMatchObject({
    path: "/providers/custom/mi-servidor",
    body: { type: "openai", base_url: "https://x.com/v1", key: "sk-1234" },
  });
  await waitFor(() => expect(screen.getByText("clave sk-…1234")).toBeInTheDocument());
});
