import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ResultCard, resultText, splitAnswer } from "./ResultCard";

const forecast = {
  outcomes: { "No antes de 2036": 0.61, "En 2032-2036": 0.16, "En 2029-2031": 0.14, "En 2026-2028": 0.09 },
  horizon_days: 3650,
  created_at: "2026-11-16T23:30:00Z",
  assumptions: [
    "Respuesta: Hay un 39% de que ocurra antes de 2036; si ocurre, lo más probable es entre 2032 y 2036.",
    "En pocas palabras: Las crisis así son raras, pero hoy hay más riesgos de lo normal.",
    "Tasa base: Barro-Ursúa, 3,6% anual",
    "Razonamiento: riesgos elevados",
  ],
};

const show = (result: any) =>
  render(
    <MemoryRouter>
      <ResultCard result={result} />
    </MemoryRouter>,
  );

describe("Tarjeta: la respuesta en palabras llanas primero", () => {
  it("titular = la respuesta, «Por qué» visible y lo técnico plegado sin repetir", () => {
    show({ kind: "forecast", forecast });
    expect(screen.getByText(/Hay un 39% de que ocurra antes de 2036/)).toBeInTheDocument();
    expect(screen.getByText("Por qué")).toBeInTheDocument();
    expect(screen.getByText(/hoy hay más riesgos/)).toBeInTheDocument();
    expect(screen.queryByText("Lo más probable")).not.toBeInTheDocument();
    expect(screen.getByText("Ver en qué se basa (2)")).toBeInTheDocument();
    expect(screen.queryByText(/^Respuesta:/)).not.toBeInTheDocument();
  });

  it("sin respuesta (previsiones antiguas) se ve como antes", () => {
    show({ kind: "forecast", forecast: { ...forecast, assumptions: ["Tasa base: x"] } });
    expect(screen.getByText("Lo más probable")).toBeInTheDocument();
    expect(screen.getByText("Probable · ≈6 de cada 10")).toBeInTheDocument();
  });

  it("al compartir va primero la respuesta", () => {
    expect(resultText("¿Cuándo?", { kind: "forecast", forecast }).split("\n")[1]).toMatch(/^Hay un 39%/);
    expect(splitAnswer(["Otro"]).answer).toBeNull();
  });
});

describe("Preguntar: sugerir Previsión para preguntas informativas", () => {
  it("distingue saber de decidir", async () => {
    const { looksInformational } = await import("../pages/Ask");
    expect(looksInformational("¿Cuándo es posible que haya una crisis a nivel mundial?")).toBe(true);
    expect(looksInformational("¿Habrá tercera guerra mundial?")).toBe(true);
    expect(looksInformational("¿Bajará el precio del alquiler este año?")).toBe(true);
    expect(looksInformational("¿Debo vender mi coche antes de que haya una crisis?")).toBe(false);
    expect(looksInformational("¿Me conviene cambiar de trabajo?")).toBe(false);
  });
});
