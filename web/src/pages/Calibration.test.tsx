import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { Calibration as Cal } from "../lib/api";
import Calibration, { calibrationSentence, skillWords } from "./Calibration";

const stats = { n: 12, brier: 0.3, chance: 0.5, skill: 0.4 };
const data: Cal = {
  n: 12,
  filters: { kinds: ["prevision", "panel"], models: ["m"] },
  summary: { ...stats, log_loss: 0.5, hit_rate: 0.6, confidence: 0.8, overconfidence: 0.2, open: 3 },
  reliability: [{ bin: 8, p_mid: 0.85, confidence: 0.8, n: 12, observed: 0.6 }],
  trend: [
    { month: "2026-10", ...stats, skill: 0.2 },
    { month: "2026-11", ...stats },
  ],
  by_kind: [
    { kind: "prevision", ...stats },
    { kind: "panel", ...stats, skill: -0.1 },
  ],
  by_horizon: [{ horizon: "corto", label: "Hasta 1 mes", ...stats }],
  sources: [{ source: "llm:m:prevision", ...stats }],
  worst: [
    {
      id: "w",
      question: "¿Ganará el equipo local?",
      source: "llm:m:prevision",
      predicted: "sí",
      predicted_p: 0.9,
      actual: "no",
      actual_p: 0.1,
      brier: 1.62,
      resolved_at: "2026-11-10T12:00:00Z",
    },
  ],
  recalibration: [],
};

afterEach(() => vi.unstubAllGlobals());

describe("Calibración en palabras", () => {
  it("compara con el azar y dice si se pasa de confiado", () => {
    expect(skillWords(0.34)).toBe("34% mejor que el azar");
    expect(skillWords(-0.2)).toBe("20% peor que el azar");
    expect(skillWords(0.01)).toBe("igual que el azar");
    expect(calibrationSentence(data.summary)).toMatch(/80% seguro y acierta el 60%\. Se pasa de confiado/);
    expect(calibrationSentence({ ...data.summary!, n: 2 })).toMatch(/pocas predicciones/);
  });

  it("muestra resumen, desglose y los peores fallos", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } })),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <Calibration />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("+40%")).toBeInTheDocument();
    expect(screen.getByText("el azar sacaría 0.500")).toBeInTheDocument();
    expect(screen.getByText("3 pendientes de resolver")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Por tipo" })).toBeInTheDocument();
    expect(screen.getByText("10% peor que el azar")).toBeInTheDocument();
    expect(screen.getByText("¿Ganará el equipo local?")).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Tipo" })).toBeInTheDocument();
  });
});
