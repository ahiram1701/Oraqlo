import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { FeedbackProvider } from "../components/ui";
import type { Forecast } from "../lib/api";
import { OpenForecast, UNDO_MS, marketStatusLabel, overdueForUser } from "./Ledger";

const forecast = {
  id: "f1",
  question: "¿Lloverá mañana?",
  outcomes: { sí: 0.7, no: 0.3 },
  source: "llm:m:prevision",
  created_at: "2026-01-01T00:00:00Z",
  horizon_days: 1,
} as unknown as Forecast;

function renderRow(f: Forecast = forecast) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <FeedbackProvider>
          <OpenForecast f={f} />
        </FeedbackProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function resolveCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([url]) => String(url).includes("/ledger/f1/resolve"));
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Predicciones: resolver con deshacer", () => {
  it("si sales de la página antes de 5 s, la resolución se envía igual", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = renderRow();
    fireEvent.click(screen.getByRole("button", { name: /Lloverá/ }));
    fireEvent.click(screen.getByRole("button", { name: /^sí/ }));
    unmount(); // navegar a otra página o filtrar con la búsqueda
    await act(async () => {
      vi.advanceTimersByTime(UNDO_MS + 10);
    });
    const calls = resolveCalls(fetchMock);
    expect(calls).toHaveLength(1);
    expect(JSON.parse(String((calls[0][1] as RequestInit).body))).toEqual({ outcome: "sí" });
  });

  it("deshacer cancela el envío", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: /Lloverá/ }));
    fireEvent.click(screen.getByRole("button", { name: /^no/ }));
    fireEvent.click(screen.getByRole("button", { name: "Deshacer" }));
    await act(async () => {
      vi.advanceTimersByTime(UNDO_MS + 10);
    });
    expect(resolveCalls(fetchMock)).toHaveLength(0);
  });
});

describe("Predicciones de Práctica vencidas", () => {
  const practice = {
    ...forecast,
    id: "p1",
    question: "[polymarket:7] ¿Pasará X?",
    source: "llm:comite",
    market_status: "proposed",
  } as unknown as Forecast;

  it("no piden que las marque el usuario: dicen en qué fase está Polymarket", () => {
    renderRow(practice);
    expect(screen.queryByText("ya debería saberse")).toBeNull();
    expect(screen.getByText("resultado propuesto, en revisión")).toBeTruthy();
  });

  it("el filtro «ya deberían saberse» solo cuenta las del usuario", () => {
    expect(overdueForUser(forecast)).toBe(true);
    expect(overdueForUser(practice)).toBe(false);
    expect(marketStatusLabel(undefined)).toMatch(/esperando/);
    expect(marketStatusLabel("disputed")).toMatch(/impugnado/);
    expect(marketStatusLabel("abierto")).toMatch(/aún la cotiza/);
  });
});
