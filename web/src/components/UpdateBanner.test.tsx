import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { shouldAutoReload } from "../lib/appVersion";
import UpdateBanner from "./UpdateBanner";

function setBundle(version: string | null) {
  document.head.querySelector('meta[name="oraqlo-version"]')?.remove();
  if (version) {
    const meta = document.createElement("meta");
    meta.name = "oraqlo-version";
    meta.content = version;
    document.head.append(meta);
  }
}

function serve(version: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ status: "ok", version }), { headers: { "Content-Type": "application/json" } })),
  );
}

const renderBanner = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <UpdateBanner />
    </QueryClientProvider>,
  );

afterEach(() => {
  vi.unstubAllGlobals();
  setBundle(null);
  sessionStorage.clear();
});

describe("UpdateBanner: interfaz vieja tras actualizar", () => {
  it("si el servidor ya es otra versión, ofrece recargar", async () => {
    setBundle("0.7.0");
    serve("0.8.0");
    // Pasado el arranque y con la app a la vista: no recarga sola, avisa.
    sessionStorage.setItem("oraqlo-reload-for", "0.8.0");
    renderBanner();
    expect(await screen.findByText(/se actualizó a la/)).toHaveTextContent("0.8.0");
    expect(screen.getByRole("button", { name: "Recargar" })).toBeEnabled();
  });

  it("con la misma versión no dice nada", async () => {
    setBundle("0.8.0");
    serve("0.8.0");
    renderBanner();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("recarga sola solo recién abierta o en segundo plano, y una vez por versión", () => {
    const later = Date.now() + 60_000;
    expect(shouldAutoReload("0.8.0", Date.now(), false)).toBe(true); // recién abierta
    expect(shouldAutoReload("0.8.0", later, false)).toBe(false); // alguien la está usando
    expect(shouldAutoReload("0.8.0", later, true)).toBe(true); // en la bandeja
    sessionStorage.setItem("oraqlo-reload-for", "0.8.0");
    expect(shouldAutoReload("0.8.0", Date.now(), true)).toBe(false); // sin bucles
  });
});
