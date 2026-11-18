import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { activeNav, badgeText } from "../App";
import { RecentRuns } from "./RecentRuns";
import { CASE_DRAFT_KEY, ResultCard } from "./ResultCard";
import { FeedbackProvider } from "./ui";

afterEach(() => vi.unstubAllGlobals());

const wrap = (ui: React.ReactNode, path = "/") =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <FeedbackProvider>
          <Routes>
            <Route path="/" element={ui} />
            <Route path="/caso" element={<p>editor abierto</p>} />
          </Routes>
        </FeedbackProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

it("cada ruta enciende su pestaña (también las que ya no están en el menú)", () => {
  expect(activeNav("/")).toBe("/");
  expect(activeNav("/historial/abc")).toBe("/");
  expect(activeNav("/calibracion")).toBe("/practica");
  expect(activeNav("/caso")).toBe("/ajustes");
  expect(activeNav("/objetivos/1")).toBe("/objetivos");
  expect([badgeText(3), badgeText(10), badgeText(79)]).toEqual(["3", "9+", "9+"]);
});

it("Preguntar enseña lo reciente y lleva al historial completo", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify([
            { id: "r1", kind: "forecast", title: "¿Lloverá?", created_at: "2026-11-01T10:00:00Z" },
            { id: "r2", kind: "goal", title: "Correr un maratón", created_at: "2026-11-02T10:00:00Z" },
          ]),
          { headers: { "Content-Type": "application/json" } },
        ),
    ),
  );
  wrap(<RecentRuns />);
  const goal = await screen.findByRole("link", { name: /Correr un maratón/ });
  expect(goal).toHaveAttribute("href", "/historial/r2");
  expect(screen.getAllByRole("link").map((l) => l.getAttribute("href"))).toEqual(["/historial", "/historial/r2", "/historial/r1"]);
});

it("«Editar y volver a simular» lleva el caso al editor", () => {
  localStorage.removeItem(CASE_DRAFT_KEY);
  const data = { question: "¿A o B?", horizon_days: 30, actions: {}, utilities: {} };
  wrap(
    <ResultCard
      result={{ kind: "cycle", forecast: null, recommendation: { action: { description: "A" } }, verdict: {}, case: data }}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Editar y volver a simular/ }));
  expect(screen.getByText("editor abierto")).toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem(CASE_DRAFT_KEY)!)).toEqual(data);
});
