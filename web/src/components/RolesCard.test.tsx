import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
import RolesCard, { catalogOf, hasOwnSettings, supportsEffort } from "./RolesCard";
import { FeedbackProvider } from "./ui";

const view = {
  providers: [
    { name: "ollama-local", kind: "ollama", api_key_env: null, key_present: null, key_hint: null },
    { name: "anthropic", kind: "anthropic", api_key_env: "ANTHROPIC_API_KEY", key_present: false, key_hint: null },
  ],
  roles: [
    { name: "estratega", provider: "ollama-local", model: "deepseek-v4.1-flash:cloud", temperature: 0.4, effort: null },
    { name: "oraculo", provider: "ollama-local", model: "gpt-oss:120b-cloud", temperature: 0.2, effort: null },
  ],
  configured: { default: { provider: "ollama-local", model: "deepseek-v4.1-flash:cloud" }, oraculo: { model: "gpt-oss:120b-cloud" } },
  web_search: { api_key_env: "OLLAMA_API_KEY", key_present: false, key_hint: null },
};
const models = {
  models: ["deepseek-v4.1-flash:cloud"],
  catalog: [
    { name: "deepseek-v4.1-flash:cloud", cloud: true, size: null, installed: true },
    { name: "gpt-oss:120b-cloud", cloud: true, size: 65e9, installed: false },
  ],
};

function mockApi() {
  const calls: { method: string; path: string; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = String(url).replace(/^\/api/, "");
      const method = init?.method ?? "GET";
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null });
      const body =
        path === "/providers"
          ? view
          : path === "/setup"
            ? { ready: true, needs_setup: false, local: true, legacy_imported: false }
            : path.startsWith("/providers/ollama-local/models")
              ? models
              : path === "/providers/ollama-local/test"
                ? { ok: true, detail: "Responde (1.2 s).", seconds: 1.2 }
                : {};
      return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("Modelos del panel", () => {
  it("helpers", () => {
    expect(catalogOf({ models: ["a"] })).toEqual([{ name: "a", cloud: false, installed: true }]);
    expect(supportsEffort("ollama") && supportsEffort("anthropic") && !supportsEffort("openai")).toBe(true);
    expect(hasOwnSettings({ provider: null, model: "" })).toBe(false);
    expect(hasOwnSettings({ model: "x" })).toBe(true);
  });

  it("elige del catálogo, prueba el modelo y marca lo propio", async () => {
    const calls = mockApi();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <FeedbackProvider>
          <RolesCard />
        </FeedbackProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("propio")).toBeInTheDocument(); // el Oráculo tiene su propio modelo
    expect(screen.getByRole("button", { name: "Restablecer" })).toBeInTheDocument();

    const rows = screen.getAllByRole("listitem");
    fireEvent.click(within(rows[1]).getByRole("button", { name: "Cambiar" })); // Estratega
    const picker = await screen.findByRole("combobox", { name: "Modelo" });
    await waitFor(() => expect(within(picker).getByRole("option", { name: /gpt-oss:120b-cloud/ })).toBeInTheDocument());
    expect(within(picker).getByRole("group", { name: "En la nube de Ollama" })).toBeInTheDocument();
    fireEvent.change(picker, { target: { value: "gpt-oss:120b-cloud" } });
    expect(screen.getByText("Esfuerzo de razonamiento")).toBeInTheDocument(); // también en Ollama

    fireEvent.click(screen.getByRole("button", { name: "Probar" }));
    expect(await screen.findByText(/Responde \(1.2 s\)/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/providers/ollama-local/test")?.body).toEqual({ model: "gpt-oss:120b-cloud" });
  });
});

describe("Probar usa lo que se aplicará de verdad", () => {
  const base = {
    providers: [],
    roles: [],
    auto: { provider: "anthropic", model: "claude-opus-5-5" },
    default_models: { "ollama-local": "llama3.1", "ollama-cloud": "gpt-oss:120b", anthropic: "claude-opus-5-5" },
    configured: { default: { provider: "ollama-local", model: "deepseek-v4.1-flash:cloud" }, oraculo: { model: "viejo:cloud" } },
  } as any;

  it("un rol que borra su modelo propio hereda el de «Todos», no el que tenía", async () => {
    const { resolveRole } = await import("./RolesCard");
    expect(resolveRole(base, "oraculo", { model: "" })).toEqual({ provider: "ollama-local", model: "deepseek-v4.1-flash:cloud" });
  });

  it("si cambia de proveedor, usa el recomendado de ese proveedor", async () => {
    const { resolveRole } = await import("./RolesCard");
    expect(resolveRole(base, "juez", { provider: "ollama-cloud", model: "" })).toEqual({ provider: "ollama-cloud", model: "gpt-oss:120b" });
  });

  it("«Todos» sin proveedor usa la elección automática", async () => {
    const { resolveRole } = await import("./RolesCard");
    expect(resolveRole(base, "default", { provider: "", model: "" })).toEqual({ provider: "anthropic", model: "claude-opus-5-5" });
  });
});
