import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { HorizonPicker, fromDays, horizonDays, type Horizon } from "./HorizonPicker";
import { ResultCard, resultText } from "./ResultCard";

const cycle = {
  kind: "cycle",
  forecast: {
    outcomes: { "no habrá": 0.93, "habrá": 0.07 },
    horizon_days: 365,
    created_at: "2026-11-16T12:00:00Z",
    assumptions: ["Razonamiento: las potencias evitan el choque directo", "Tasa base: 0 en 80 años"],
  },
  verdict: { decision: "revisar", confidence: 0.76, rationale: "Faltan reservas.", p_success: 0.62, review_triggers: [] },
  recommendation: { action: { description: "Mantener la rutina actual" } },
};

describe("Análisis completo: primero la respuesta a la pregunta", () => {
  it("muestra la respuesta antes de qué hacer", () => {
    render(
      <MemoryRouter>
        <ResultCard result={cycle} />
      </MemoryRouter>,
    );
    const answer = screen.getByRole("heading", { name: "Respuesta" });
    const todo = screen.getByRole("heading", { name: "Qué hacer" });
    expect(answer.compareDocumentPosition(todo) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByText("no habrá").length).toBeGreaterThan(0); // en grande y en las barras
    expect(screen.getByText(/Se sabrá hacia el/)).toBeInTheDocument();
    expect(screen.getByText(/Tasa base: 0 en 80 años/)).toBeInTheDocument();
    expect(screen.getByText(/Probabilidad de que salga bien/)).toHaveTextContent("62");
    expect(screen.getByText("Mantener la rutina actual")).toBeInTheDocument();
  });

  it("al compartir, la respuesta va primero", () => {
    const text = resultText("¿Habrá tercera guerra mundial?", cycle);
    expect(text.indexOf("Lo más probable: no habrá")).toBeLessThan(text.indexOf("Qué hacer"));
  });
});

describe("Pensando a: plazos libres", () => {
  it("convierte cantidades y unidades a días, con el límite del servidor", () => {
    expect(horizonDays({ preset: null, amount: 2, unit: "anios", date: "" })).toBe(730);
    expect(horizonDays({ preset: null, amount: 6, unit: "semanas", date: "" })).toBe(42);
    expect(horizonDays({ preset: null, amount: 50, unit: "anios", date: "" })).toBe(3650);
    expect(fromDays(1825).preset).toBe(1825); // 5 años está en la lista
    expect(fromDays(45)).toMatchObject({ preset: null, amount: 45, unit: "dias" });
    expect(fromDays(60)).toMatchObject({ preset: null, amount: 2, unit: "meses" });
  });

  it("«Otro…» permite elegir 18 meses", () => {
    let current: Horizon = fromDays(90);
    function Harness() {
      const [h, setH] = useState(current);
      current = h;
      return <HorizonPicker value={h} onChange={setH} />;
    }
    render(<Harness />);
    fireEvent.change(screen.getByLabelText("Pensando a"), { target: { value: "otro" } });
    fireEvent.change(screen.getByLabelText("Cantidad"), { target: { value: "18" } });
    fireEvent.change(screen.getByLabelText("Unidad"), { target: { value: "meses" } });
    expect(horizonDays(current)).toBe(540);
  });
});
