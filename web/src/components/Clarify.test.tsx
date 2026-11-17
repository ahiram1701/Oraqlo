import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import type { QuestionsResult } from "../lib/api";
import { ClarifyCard } from "./Clarify";

const RESULT: QuestionsResult = {
  kind: "questions",
  mode: "forecast",
  question: "¿Dejo mi trabajo?",
  questions: [
    { id: "p1", text: "¿Cuántos meses de gastos tienes ahorrados?", why: "Cuánto aguantas.", options: ["Menos de 3", "Más de 6"] },
    { id: "p2", text: "¿Emprenderías con un socio?", why: "", options: [] },
  ],
};

function mockFetch() {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ job_id: "nuevo" }), { status: 202 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => vi.unstubAllGlobals());

describe("ClarifyCard", () => {
  it("envía solo lo respondido y sigue con el análisis nuevo", async () => {
    const fetch = mockFetch();
    const onContinue = vi.fn();
    render(<ClarifyCard jobId="j1" result={RESULT} onContinue={onContinue} />);
    const submit = screen.getByRole("button", { name: "Responder y seguir" });
    expect(submit).toBeDisabled(); // sin respuestas, mejor «Continuar sin responder»
    fireEvent.click(screen.getByRole("button", { name: "Más de 6" }));
    expect(screen.getByLabelText(/meses de gastos/)).toHaveValue("Más de 6");
    fireEvent.click(submit);
    await waitFor(() => expect(onContinue).toHaveBeenCalledWith("nuevo"));
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/jobs/j1/answer");
    expect(JSON.parse(String(init.body))).toEqual({ answers: { p1: "Más de 6" } });
  });

  it("«Continuar sin responder» manda answers: null", async () => {
    const fetch = mockFetch();
    const onContinue = vi.fn();
    render(<ClarifyCard jobId="j1" result={RESULT} onContinue={onContinue} />);
    fireEvent.click(screen.getByRole("button", { name: "Continuar sin responder" }));
    await waitFor(() => expect(onContinue).toHaveBeenCalledWith("nuevo"));
    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ answers: null });
  });
});
