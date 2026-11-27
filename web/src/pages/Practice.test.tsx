import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { PracticeState } from "../lib/api";
import Practice from "./Practice";

const state: PracticeState = {
  auto: true,
  interval_min: 60,
  last_run: "2026-11-26T10:00:00Z",
  next_run: "2026-11-26T11:00:00Z",
  running: false,
  research: false,
  advice: "Sobreconfías: reparte más la probabilidad.",
  recent: [],
  duels: {
    items: [],
    summary: {
      resolved: 4,
      open: 1,
      won: 3,
      lost: 1,
      tied: 0,
      oraqlo_brier: 0.2,
      market_brier: 0.3,
      chance_brier: 0.5,
      oraqlo_skill: 0.6,
      market_skill: 0.4,
    },
  },
};

function renderPractice(data: PracticeState) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } })),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <Practice />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Práctica", () => {
  it("anuncia el marcador y dice quién va ganando sin depender del color", async () => {
    renderPractice(state);
    const headline = await screen.findByText(/De 4 duelos resueltos: Oraqlo gana 3 y el mercado 1/);
    expect(headline).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("group", { name: /^Oraqlo: .*va ganando$/ })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: /^Mercado \(Polymarket\): (?!.*va ganando)/ })).toBeInTheDocument();
  });

  it("muestra el consejo y avisa de que predice sin investigar", async () => {
    renderPractice(state);
    expect(await screen.findByText(/Consejo: Sobreconfías/)).toBeInTheDocument();
    expect(screen.getByText(/Añade la clave de Ollama/)).toBeInTheDocument();
  });

  it("con una pasada en curso, el botón está ocupado y desactivado", async () => {
    renderPractice({ ...state, running: true });
    const button = await screen.findByRole("button", { name: "Pasada en curso…" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });
});
