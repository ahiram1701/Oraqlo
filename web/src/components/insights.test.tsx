import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { modelLabel, type Forecast, type WatchView } from "../lib/api";
import { OpenForecast } from "../pages/Ledger";
import { ResultCard } from "./ResultCard";
import { FeedbackProvider } from "./ui";

const wrap = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <FeedbackProvider>{ui}</FeedbackProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

const watch: WatchView = {
  root: "f0",
  enabled: true,
  kind: "forecast",
  signals: [{ text: "Que suban los tipos", look_for: "tipos", favors: "baja", triggered_at: "2026-11-10T00:00:00Z" }],
  history: [
    { at: "2026-11-01T00:00:00Z", id: "f0", outcomes: { sube: 0.7, baja: 0.3 }, why: "Previsión inicial." },
    { at: "2026-11-10T00:00:00Z", id: "f1", outcomes: { sube: 0.4, baja: 0.6 }, why: "Subieron los tipos." },
  ],
  versions: 2,
  last_check: "2026-11-10T00:00:00Z",
};

describe("Lo nuevo de la respuesta", () => {
  it("enseña el mercado, la estimación por partes y lo que vigila", () => {
    wrap(
      <ResultCard
        result={{
          kind: "forecast",
          forecast: { outcomes: { sí: 0.6, no: 0.4 }, assumptions: [] },
          voices: {
            market: {
              platform: "polymarket",
              question: "Will X happen?",
              url: "https://polymarket.com/market/x",
              outcomes: { Yes: 0.8, No: 0.2 },
              crowd: "12.000 $ apostados",
              why: "",
            },
            structure: { mode: "y", yes_label: "sí", parts: [{ text: "¿Abre?", p: 0.5 }, { text: "¿Gana?", p: 0.4 }], p_yes: 0.2 },
          },
          watch,
        }}
      />,
    );
    expect(screen.getByRole("link", { name: "«Will X happen?»" })).toHaveAttribute("href", "https://polymarket.com/market/x");
    expect(screen.getByText(/Tienen que pasar todas/)).toBeInTheDocument();
    expect(screen.getByText(/Juntas dan 20%/)).toBeInTheDocument();
    expect(screen.getByText(/Vigilando 1 señales/)).toBeInTheDocument();
  });

  it("el análisis completo dice qué cambiaría la recomendación, el pre-mortem y el rival", () => {
    wrap(
      <ResultCard
        result={{
          kind: "cycle",
          forecast: null,
          recommendation: { action: { description: "Bajar precios" } },
          verdict: {
            decision: "emitir",
            confidence: 0.7,
            premortem: [{ cause: "Nadie compró", signal: "Ventas < 10" }],
          },
          analysis: { best: "a", summary: ["Si la probabilidad de «bien» bajara de 55%, convendría más «B»."] },
          rival: { description: "la competencia", responses: { a: [{ response: "Iguala precios", p: 0.9 }] } },
          case: { actions: { a: { description: "Bajar precios" } } },
        }}
      />,
    );
    expect(screen.getByText("Qué haría cambiar la recomendación")).toBeInTheDocument();
    expect(screen.getByText(/bajara de 55%/)).toBeInTheDocument();
    expect(screen.getByText("Nadie compró")).toBeInTheDocument();
    expect(screen.getByText(/Señal temprana: Ventas < 10/)).toBeInTheDocument();
    expect(screen.getByText("Iguala precios")).toBeInTheDocument();
  });

  it("un objetivo dice qué hacer primero", () => {
    wrap(
      <ResultCard
        result={{
          kind: "goal",
          goal_id: "g1",
          baseline_p: 0.2,
          with_plan_p: 0.5,
          uplift: 0.3,
          plan: {
            summary: "plan",
            steps: [],
            levers: [{ text: "Llamar a 5 clientes", impact: "medio", effort: "bajo", uplift: 8 }],
          },
          verdict: {},
        }}
      />,
    );
    expect(screen.getByText("Haz esto primero")).toBeInTheDocument();
    expect(screen.getByText("+8 pts")).toBeInTheDocument();
  });
});

describe("Predicciones vigiladas y numéricas", () => {
  const base = {
    id: "f1",
    question: "¿Cuánto costará?",
    outcomes: { "menos de 10": 0.2, "de 10 a 20": 0.5, "20 o más": 0.3 },
    source: "llm:m:prevision",
    created_at: "2026-11-01T00:00:00Z",
    horizon_days: 60,
    assumptions: [],
  };

  it("pide la cifra que salió en una pregunta de cantidad", () => {
    wrap(<OpenForecast f={{ ...base, numeric: { unit: "€", bins: {} } } as unknown as Forecast} />);
    fireEvent.click(screen.getByRole("button", { name: /Cuánto costará/ }));
    expect(screen.getByLabelText("Cifra que salió")).toHaveAttribute("placeholder", "O escribe la cifra que salió (€)…");
  });

  it("enseña cómo se movió y qué vigila", () => {
    wrap(<OpenForecast f={{ ...base, outcomes: { baja: 0.6, sube: 0.4 }, watch } as unknown as Forecast} />);
    expect(screen.getByText("vigilada · 2 versiones")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Cuánto costará/ }));
    expect(screen.getByText(/«baja»: 30% → 60%/)).toBeInTheDocument();
    expect(screen.getByText(/Último cambio: Subieron los tipos/)).toBeInTheDocument();
    expect(screen.getByText("ya pasó")).toBeInTheDocument();
  });
});

it("nombres de las voces nuevas", () => {
  expect(modelLabel("mercado:polymarket")).toBe("Mercado (Polymarket)");
  expect(modelLabel("estructura")).toBe("Estimación por partes");
});
