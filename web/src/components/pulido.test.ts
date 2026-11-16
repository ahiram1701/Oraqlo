import { forecastKind } from "../lib/api";
import { isoInDays, localISODate } from "./HorizonPicker";

describe("pulido de lo nuevo", () => {
  it("una previsión antigua sin tipo no es de Práctica", () => {
    expect(forecastKind("llm:qwen3:8b", "¿Lloverá?")).toBe("prevision");
    expect(forecastKind("llm:deepseek:cloud", "[polymarket:1] ¿Ganará?")).toBe("practica");
    expect(forecastKind("polymarket:mercado")).toBe("practica");
    expect(forecastKind("llm:m:panel")).toBe("panel");
  });

  it("las fechas salen en el calendario local, no en UTC", () => {
    // 22:30 del 16 de noviembre (hora local): mañana es el 17, aunque en UTC ya sea día 17.
    const night = new Date(2026, 10, 16, 22, 30);
    expect(localISODate(night)).toBe("2026-11-16");
    expect(isoInDays(1, night)).toBe("2026-11-17");
  });
});
