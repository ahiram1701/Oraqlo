import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { ResultCard, resultText } from "./ResultCard";
import { FeedbackProvider, plainProbability, useFeedback } from "./ui";

describe("ResultCard", () => {
  it("muestra el escenario más probable con lectura llana", () => {
    render(
      <MemoryRouter>
        <ResultCard result={{ kind: "forecast", forecast: { outcomes: { baja: 0.3, sube: 0.7 }, assumptions: [] } }} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Lo más probable")).toBeInTheDocument();
    expect(screen.getByText(/Probable · ≈7 de cada 10/)).toBeInTheDocument();
  });

  it("traduce el veredicto del juez", () => {
    render(
      <MemoryRouter>
        <ResultCard
          result={{
            kind: "cycle",
            verdict: { decision: "emitir", confidence: 0.82, rationale: "ok" },
            recommendation: { action: { description: "Aceptar el trabajo" } },
          }}
        />
      </MemoryRouter>,
    );
    expect(screen.getAllByText("Recomendado").length).toBeGreaterThan(0);
    expect(screen.queryByText("emitir")).not.toBeInTheDocument();
  });

  it("el texto para compartir empieza por lo más probable", () => {
    const text = resultText("¿Sube?", { kind: "forecast", forecast: { outcomes: { no: 0.2, sí: 0.8 } } });
    expect(text).toContain("Lo más probable: sí (80%)");
  });
});

describe("plainProbability", () => {
  it("usa palabras y frecuencias", () => {
    expect(plainProbability(0.95)).toBe("Casi seguro · ≈10 de cada 10");
    expect(plainProbability(0.5)).toBe("Incierto · ≈5 de cada 10");
  });
});

function Harness({ onUndo, onAnswer }: { onUndo: () => void; onAnswer: (ok: boolean) => void }) {
  const { confirm, toast } = useFeedback();
  return (
    <>
      <button onClick={() => toast("Marcada", { undo: onUndo })}>avisar</button>
      <button onClick={async () => onAnswer(await confirm({ title: "¿Borrar?", confirm: "Borrar", danger: true }))}>preguntar</button>
    </>
  );
}

describe("FeedbackProvider", () => {
  it("Deshacer llama al callback y cierra el aviso", () => {
    const onUndo = vi.fn();
    render(
      <FeedbackProvider>
        <Harness onUndo={onUndo} onAnswer={() => {}} />
      </FeedbackProvider>,
    );
    fireEvent.click(screen.getByText("avisar"));
    fireEvent.click(screen.getByRole("button", { name: "Deshacer" }));
    expect(onUndo).toHaveBeenCalledOnce();
    expect(screen.queryByText("Marcada")).not.toBeInTheDocument();
  });

  it("el aviso desaparece solo", () => {
    vi.useFakeTimers();
    render(
      <FeedbackProvider>
        <Harness onUndo={() => {}} onAnswer={() => {}} />
      </FeedbackProvider>,
    );
    fireEvent.click(screen.getByText("avisar"));
    expect(screen.getByText("Marcada")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(5100));
    expect(screen.queryByText("Marcada")).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("la confirmación responde sí, y Escape responde no", async () => {
    const answers: boolean[] = [];
    render(
      <FeedbackProvider>
        <Harness onUndo={() => {}} onAnswer={(ok) => answers.push(ok)} />
      </FeedbackProvider>,
    );
    fireEvent.click(screen.getByText("preguntar"));
    expect(screen.getByRole("alertdialog", { name: "¿Borrar?" })).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Borrar" })));
    fireEvent.click(screen.getByText("preguntar"));
    await act(async () => fireEvent.keyDown(window, { key: "Escape" }));
    expect(answers).toEqual([true, false]);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});
