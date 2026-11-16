import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import App from "../App";
import ProvidersCard from "../components/ProvidersCard";
import { FeedbackProvider } from "../components/ui";

type Handler = (method: string, body: any) => unknown;

/** fetch falso: ruta (sin /api) → respuesta. Registra las llamadas. */
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

const PROVIDERS = {
  providers: [
    { name: "anthropic", kind: "anthropic", api_key_env: "ANTHROPIC_API_KEY", key_present: false, key_hint: null },
    { name: "ollama-local", kind: "ollama", api_key_env: null, key_present: null, key_hint: null },
  ],
  roles: [{ name: "juez", provider: "ollama-local", model: "llama3.1", temperature: 0.1, effort: null }],
  configured: {},
};

function renderAt(path: string, ui = <App />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <FeedbackProvider>{ui}</FeedbackProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test("sin modelo listo, la app lleva a la bienvenida y conecta con la clave pegada", async () => {
  const calls = mockApi({
    "/setup": { ready: false, needs_setup: true, local: true, legacy_imported: false },
    "/providers": PROVIDERS,
    "/goals": [],
    "PUT /keys/ANTHROPIC_API_KEY": PROVIDERS,
    "PUT /config": { roles: { default: { provider: "anthropic" } }, updates_mode: "auto", allowed_users: [] },
    "POST /providers/anthropic/test": { name: "anthropic", ok: true, detail: "Responde.", fix: null },
    "/server": { port: 8765, local_url: "http://127.0.0.1:8765", managed: false, tailscale: { installed: false, state: "missing", enabled: false, url: null, serving_port: null } },
    "/me": { login: null, via: "local" },
  });
  renderAt("/");
  expect(await screen.findByText("Te damos la bienvenida a Oraqlo", {}, { timeout: 8000 })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /Claude \(Anthropic\)/ }));
  fireEvent.change(screen.getByLabelText("Clave de API"), { target: { value: "sk-ant-123" } });
  fireEvent.click(screen.getByRole("button", { name: "Conectar y probar" }));

  expect(await screen.findByText(/Oraqlo ya puede pensar/)).toBeInTheDocument();
  expect(calls.find((c) => c.path === "/keys/ANTHROPIC_API_KEY")?.body).toEqual({ value: "sk-ant-123" });
  expect(calls.find((c) => c.method === "PUT" && c.path === "/config")?.body).toEqual({
    roles: { default: { provider: "anthropic", model: null } },
  });
});

test("con un modelo listo no hay redirección", async () => {
  mockApi({
    "/setup": { ready: true, needs_setup: false, local: true, legacy_imported: false },
    "/goals": [],
  });
  renderAt("/");
  await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Preguntar"));
  expect(screen.queryByText("Te damos la bienvenida a Oraqlo")).not.toBeInTheDocument();
});

test("desde el celular las claves no se pueden cambiar", async () => {
  mockApi({
    "/setup": { ready: true, needs_setup: false, local: false, legacy_imported: false },
    "/providers": PROVIDERS,
  });
  renderAt("/ajustes", <ProvidersCard />);
  const add = await screen.findByRole("button", { name: "Añadir clave" });
  expect(add).toBeDisabled();
  expect(screen.getByText(/Solo se puede cambiar desde el PC/)).toBeInTheDocument();
});

test("en el celular, «Más» abre el resto de secciones y se marca si estás en una de ellas", async () => {
  mockApi({
    "/setup": { ready: true, needs_setup: false, local: true, legacy_imported: false },
    "/providers": PROVIDERS,
    "/goals": [],
    "/calibration": { n: 0, sources: [], reliability: [] },
  });
  renderAt("/calibracion");
  const more = await screen.findByRole("button", { name: "Más" });
  expect(more).toHaveAttribute("aria-current", "page");
  expect(screen.queryByRole("group", { name: "Más secciones" })).not.toBeInTheDocument();
  fireEvent.click(more);
  expect(more).toHaveAttribute("aria-expanded", "true");
  const group = screen.getByRole("group", { name: "Más secciones" });
  expect(within(group).getByRole("link", { name: "Ajustes" })).toHaveAttribute("href", "/ajustes");
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("group", { name: "Más secciones" })).not.toBeInTheDocument();
});
