import { forecastKind, modelLabel, parseDate, sourceLabel } from "../lib/api";
import { fmtSkill } from "../lib/score";
import { isoInDays, localISODate } from "./HorizonPicker";

describe("pulido de lo nuevo", () => {
  it("el comité y sus miembros tienen nombre legible", () => {
    expect(modelLabel("comite")).toBe("Comité de modelos");
    expect(sourceLabel("llm:comite")).toBe("Comité de modelos · práctica");
    expect(sourceLabel("llm:comite:prevision")).toBe("Comité de modelos · predicciones");
    expect(sourceLabel("llm:kimi-k3:miembro")).toBe("kimi-k3 · solo, dentro del comité");
  });

  it("una previsión antigua sin tipo no es de Práctica", () => {
    expect(forecastKind("llm:qwen3:8b", "¿Lloverá?")).toBe("prevision");
    expect(forecastKind("llm:deepseek:cloud", "[polymarket:1] ¿Ganará?")).toBe("practica");
    expect(forecastKind("polymarket:mercado")).toBe("practica");
    expect(forecastKind("llm:m:panel")).toBe("panel");
  });

  it("las fechas de SQLite (UTC sin zona) se leen como UTC", () => {
    expect(parseDate("2026-11-18 02:51:07").toISOString()).toBe("2026-11-18T02:51:07.000Z");
    expect(parseDate("2026-11-18T02:51:07+00:00").toISOString()).toBe("2026-11-18T02:51:07.000Z");
  });

  it("la habilidad frente al azar con signo", () => {
    expect([0.181, -0.2, 0.001, null].map(fmtSkill)).toEqual(["+18%", "−20%", "0%", "—"]);
  });

  it("las fechas salen en el calendario local, no en UTC", () => {
    // 22:30 del 16 de noviembre (hora local): mañana es el 17, aunque en UTC ya sea día 17.
    const night = new Date(2026, 10, 16, 22, 30);
    expect(localISODate(night)).toBe("2026-11-16");
    expect(isoInDays(1, night)).toBe("2026-11-17");
  });
});
