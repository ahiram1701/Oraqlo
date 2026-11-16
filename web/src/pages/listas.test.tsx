import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, renderHook, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { FeedbackProvider } from "../components/ui";
import type { Forecast, Goal, ResolvedForecast, RunSummary } from "../lib/api";
import { useStoredState } from "../lib/useStoredState";
import { filterGoals, sortGoals } from "./Goals";
import { sortRuns } from "./History";
import Ledger, { accuracyBand, sortOpen, sortResolved } from "./Ledger";

const day = 864e5;
const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();

function forecast(id: string, source: string, createdDaysAgo: number, horizon: number, top = 0.6): Forecast {
  return {
    id,
    question: `¿${id}?`,
    source,
    outcomes: { sí: top, no: 1 - top },
    point: null,
    interval: null,
    horizon_days: horizon,
    assumptions: [],
    created_at: iso(createdDaysAgo * day),
  } as unknown as Forecast;
}

function resolved(id: string, source: string, brier: number, resolvedDaysAgo: number): ResolvedForecast {
  return { ...forecast(id, source, 30, 1), actual_outcome: "sí", brier, resolved_at: iso(resolvedDaysAgo * day) } as ResolvedForecast;
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("orden y filtros (funciones puras)", () => {
  it("pendientes: por cuándo se sabrá, recientes o más seguras", () => {
    const list = [forecast("tarde", "llm:m:prevision", 1, 60), forecast("ya", "llm:m:prevision", 10, 1, 0.9), forecast("pronto", "llm:m:prevision", 2, 5)];
    expect(sortOpen(list, "se-sabra").map((f) => f.id)).toEqual(["ya", "pronto", "tarde"]);
    expect(sortOpen(list, "recientes").map((f) => f.id)).toEqual(["tarde", "pronto", "ya"]);
    expect(sortOpen(list, "probable")[0].id).toBe("ya");
  });

  it("resueltas: por acierto y bandas", () => {
    const list = [resolved("malo", "llm:m:prevision", 0.8, 1), resolved("bueno", "llm:m:prevision", 0.05, 3)];
    expect(sortResolved(list, "mejor")[0].id).toBe("bueno");
    expect(sortResolved(list, "peor")[0].id).toBe("malo");
    expect(sortResolved(list, "resueltas")[0].id).toBe("malo");
    expect([0.05, 0.3, 0.9].map(accuracyBand)).toEqual(["bueno", "flojo", "fallo"]);
  });

  it("historial y objetivos", () => {
    const runs = [
      { id: "1", kind: "forecast", title: "zeta", created_at: "2026-01-01" },
      { id: "2", kind: "cycle", title: "Alfa", created_at: "2026-02-01" },
    ] as RunSummary[];
    expect(sortRuns(runs, "az")[0].title).toBe("Alfa");
    expect(sortRuns(runs, "antiguos")[0].id).toBe("1");

    const goal = (id: string, status: string, p: number, p0: number, deadline: string, overdue: number[] = []) =>
      ({ id, goal: `Objetivo ${id}`, status, current_p: p, initial_p: p0, deadline, created_at: "2026-01-01", overdue }) as unknown as Goal;
    const goals = [goal("a", "activo", 0.4, 0.2, "2027-01-01", [0]), goal("b", "logrado", 0.9, 0.8, "2026-06-01"), goal("c", "activo", 0.7, 0.7, "2026-12-01")];
    expect(filterGoals(goals, "vencidos", "").map((g) => g.id)).toEqual(["a"]);
    expect(filterGoals(goals, "activo", "objetivo c").map((g) => g.id)).toEqual(["c"]);
    expect(sortGoals(goals, "mejora")[0].id).toBe("a");
    expect(sortGoals(goals, "fecha")[0].id).toBe("b");
  });

  it("useStoredState recuerda y aguanta datos viejos", () => {
    localStorage.setItem("k", JSON.stringify(42)); // guardado con otro tipo
    const { result } = renderHook(() => useStoredState("k", "todas"));
    expect(result.current[0]).toBe("todas");
    act(() => result.current[1]("vencidas"));
    expect(JSON.parse(localStorage.getItem("k")!)).toBe("vencidas");
  });
});

describe("Predicciones: filtrar y quitar filtros", () => {
  it("filtra por tipo, cuenta «x de y» y «Quitar filtros» lo devuelve todo", async () => {
    const data = {
      open: [forecast("rapida", "llm:m:prevision", 1, 30), forecast("meta", "llm:m:objetivo", 1, 30)],
      resolved: [resolved("vieja", "llm:m:prevision", 0.1, 2)],
      overdue: 0,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } })),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <FeedbackProvider>
            <Ledger />
          </FeedbackProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const tipo = await screen.findByRole("radiogroup", { name: "Tipo" });
    fireEvent.click(within(tipo).getByRole("radio", { name: /Objetivos/ }));
    expect(screen.getByText("¿meta?")).toBeInTheDocument();
    expect(screen.queryByText("¿rapida?")).not.toBeInTheDocument();
    expect(screen.getByText("1 de 2")).toBeInTheDocument();
    // Ninguna resuelta es de objetivos: se ofrece quitar los filtros.
    fireEvent.click(screen.getByRole("button", { name: "Quitar filtros" }));
    expect(screen.getByText("¿rapida?")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("oraqlo.ledger.kind")!)).toBe("todas");
  });
});
