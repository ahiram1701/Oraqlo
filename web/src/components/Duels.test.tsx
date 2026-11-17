import { fireEvent, render, screen, within } from "@testing-library/react";
import type { Duel } from "../lib/api";
import { displayQuestion } from "../lib/api";
import { DuelsCard, duelHeadline, filterDuels, sortDuels } from "./Duels";

const duel = (id: string, over: Partial<Duel>): Duel => ({
  market_id: id,
  question: `¿Pregunta ${id}?`,
  label: "Yes",
  oraqlo_p: 0.5,
  market_p: 0.5,
  created_at: "2026-11-10T00:00:00Z",
  closes_at: "2026-11-20T00:00:00Z",
  status: "resuelto",
  actual: "Yes",
  resolved_at: "2026-11-12T00:00:00Z",
  oraqlo_brier: 0.2,
  market_brier: 0.3,
  chance_brier: 0.5,
  winner: "oraqlo",
  ...over,
});

const items = [
  duel("1", {}),
  duel("2", { winner: "mercado", oraqlo_p: 0.1, market_p: 0.9 }),
  duel("3", { status: "abierto", winner: null, actual: null, closes_at: "2026-11-18T00:00:00Z" }),
];

beforeEach(() => localStorage.clear());

describe("Duelos con el mercado", () => {
  it("resume, filtra y ordena", () => {
    expect(duelHeadline({ resolved: 10, open: 2, won: 3, lost: 6, tied: 1, oraqlo_brier: 0.4, market_brier: 0.1, chance_brier: 0.5 })).toBe(
      "Oraqlo gana 3 de 10 duelos (1 empate).",
    );
    expect(duelHeadline(undefined)).toMatch(/ningún duelo/);
    expect(filterDuels(items, "perdidos").map((d) => d.market_id)).toEqual(["2"]);
    expect(filterDuels(items, "abiertos").map((d) => d.market_id)).toEqual(["3"]);
    expect(sortDuels(items, "diferencia")[0].market_id).toBe("2");
    expect(sortDuels(items, "cierre")[0].market_id).toBe("3");
  });

  it("muestra cada duelo y deja filtrar", () => {
    render(<DuelsCard items={items} />);
    expect(screen.getByText("Ganó Oraqlo")).toBeInTheDocument();
    expect(screen.getByText("Ganó el mercado")).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("radiogroup", { name: "Duelos" })).getByRole("radio", { name: /Abiertos/ }));
    expect(screen.queryByText("Ganó Oraqlo")).not.toBeInTheDocument();
    expect(screen.getByText("¿Pregunta 3?")).toBeInTheDocument();
  });

  it("oculta la etiqueta interna del mercado", () => {
    expect(displayQuestion("[polymarket:123] ¿Ganará Ana?")).toBe("¿Ganará Ana?");
    expect(displayQuestion("¿Lloverá?")).toBe("¿Lloverá?");
  });
});

describe("Duelos ya decididos al predecir", () => {
  it("se explican y no cuentan", async () => {
    const { decidedNote } = await import("./Duels");
    const s = { resolved: 7, open: 14, won: 1, lost: 6, tied: 0, already_decided: 11, oraqlo_brier: 0.43, market_brier: 0.29, chance_brier: 0.5 };
    expect(decidedNote(s)).toMatch(/^11 no cuentan: cuando Oraqlo predijo, el mercado ya sabía el resultado/);
    expect(decidedNote({ ...s, already_decided: 0 })).toBeNull();
    render(<DuelsCard items={[duel("9", { winner: null, already_decided: true })]} />);
    expect(screen.getByText("Ya se sabía")).toBeInTheDocument();
  });
});
