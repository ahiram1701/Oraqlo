import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { friendlyError } from "../lib/api";
import type { JobState } from "../lib/useJob";
import { JobTimeline, expectedPhases } from "./JobView";
import { ProbBars } from "./ProbBars";
import { ResultCard, rankAssumptions } from "./ResultCard";
import { ErrorNote, FeedbackProvider, useFeedback } from "./ui";

afterEach(() => vi.unstubAllGlobals());

const wrap = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe("ResultCard: incertidumbre y contexto", () => {
  it("una previsión numérica muestra el punto y el intervalo", () => {
    wrap(
      <ResultCard
        result={{
          kind: "forecast",
          forecast: { outcomes: {}, point: 1250, interval: [900, 1600], interval_level: 0.8, assumptions: [] },
        }}
      />,
    );
    expect(screen.getByText(/1\.250|1250/)).toBeInTheDocument();
    expect(screen.getByText(/con un 80% de probabilidad/)).toBeInTheDocument();
  });

  it("dice cuándo se sabrá y enseña primero la tasa base", () => {
    wrap(
      <ResultCard
        result={{
          kind: "forecast",
          forecast: {
            outcomes: { sí: 0.6, no: 0.4 },
            created_at: "2026-01-01T12:00:00Z",
            horizon_days: 31,
            assumptions: ["s1", "s2", "s3", "Tasa base: 3 de cada 10"],
          },
        }}
      />,
    );
    expect(screen.getByText(/Se sabrá hacia el 1 de febrero de 2026/)).toBeInTheDocument();
    expect(screen.getByText("Tasa base: 3 de cada 10")).toBeVisible();
    expect(screen.getByText(/Ver 1 supuesto\(s\) más/)).toBeInTheDocument();
  });

  it("un análisis sin recomendación no rompe la tarjeta", () => {
    wrap(<ResultCard result={{ kind: "cycle", verdict: { decision: "revisar", confidence: 0.5 } }} />);
    expect(screen.getByText("El panel no llegó a una recomendación.")).toBeInTheDocument();
  });

  it("ordena los supuestos", () => {
    expect(rankAssumptions(["x", "Razonamiento: r", "Tasa base: t"])).toEqual(["Tasa base: t", "Razonamiento: r", "x"]);
  });
});

describe("JobTimeline", () => {
  const running: JobState = { events: [], status: "running", result: null, error: null, runId: null };

  it("permite cancelar y avisa de que termina el paso en curso", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<JobTimeline job={running} kind="forecast" jobId="j1" />);
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(await screen.findByText(/Cancelando/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/jobs/j1/cancel", expect.objectContaining({ method: "POST" }));
  });

  it("en cola lo dice, y un trabajo cancelado también", () => {
    const { rerender } = wrap(<JobTimeline job={{ ...running, status: "queued" }} />);
    expect(screen.getByText(/En cola/)).toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <JobTimeline job={{ ...running, status: "cancelled" }} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/Cancelado/)).toBeInTheDocument();
  });

  it("con investigación, «investigar» es el primer paso; los objetivos tienen pasos", () => {
    expect(expectedPhases("forecast", true)).toEqual(["investigar", "prever"]);
    expect(expectedPhases("case", true)).not.toContain("investigar");
    expect(expectedPhases("goal")).toContain("con_plan");
  });
});

describe("errores claros", () => {
  it("traduce los códigos a frases", () => {
    expect(friendlyError(502, "Bad Gateway")).toMatch(/no responde ahora mismo/);
    expect(friendlyError(404, "Not Found")).toMatch(/No se encontró/);
    expect(friendlyError(400, "La pregunta es muy corta")).toBe("La pregunta es muy corta");
  });

  it("ErrorNote ofrece reintentar", () => {
    const retry = vi.fn();
    render(<ErrorNote error={new Error("falló")} onRetry={retry} />);
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(retry).toHaveBeenCalled();
  });
});

describe("accesibilidad", () => {
  it("las barras de probabilidad se leen como medidores", () => {
    render(<ProbBars outcomes={{ sube: 0.7, baja: 0.3 }} />);
    const meter = screen.getByRole("meter", { name: "sube" });
    expect(meter).toHaveAttribute("aria-valuenow", "70");
  });

  it("el diálogo atrapa el foco y lo devuelve al cerrar", async () => {
    function Opener() {
      const { confirm } = useFeedback();
      const [answer, setAnswer] = useState<string>("");
      return (
        <>
          <button onClick={() => confirm({ title: "¿Borrar?" }).then((ok) => setAnswer(String(ok)))}>Abrir</button>
          <span>{answer}</span>
        </>
      );
    }
    render(
      <FeedbackProvider>
        <Opener />
      </FeedbackProvider>,
    );
    const opener = screen.getByRole("button", { name: "Abrir" });
    opener.focus();
    fireEvent.click(opener);
    const confirmBtn = await screen.findByRole("button", { name: "Confirmar" });
    expect(confirmBtn).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab" }); // del último, vuelve al primero
    expect(screen.getByRole("button", { name: "Cancelar" })).toHaveFocus();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(opener).toHaveFocus());
    expect(screen.getByText("false")).toBeInTheDocument();
  });
});
