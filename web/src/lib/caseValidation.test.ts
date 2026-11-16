import { validateCase } from "./caseValidation";

const ok = {
  question: "¿Qué hacemos?",
  actions: { a: { description: "A", outcomes: { gana: 0.6, pierde: 0.4 } } },
  utilities: { gana: 1, pierde: -1 },
};

test("un caso válido no tiene avisos", () => {
  expect(validateCase(ok)).toEqual([]);
});

test("detecta probabilidades que no suman 1 y utilidades que faltan", () => {
  const issues = validateCase({
    ...ok,
    actions: { a: { outcomes: { gana: 0.5, empate: 0.2 } } },
  });
  expect(issues.some((i) => i.includes("suman 0.70"))).toBe(true);
  expect(issues.some((i) => i.includes('"empate"'))).toBe(true);
});

test("pide pregunta y acciones", () => {
  expect(validateCase({})).toEqual(expect.arrayContaining(['Falta "question".', 'Define al menos una acción en "actions".']));
});

import { describeValidation } from "./api";

test("los errores de validación del servidor se explican en español", () => {
  expect(
    describeValidation([{ loc: ["body", "question"], type: "string_too_long", msg: "String should have at most 2000 characters", ctx: { max_length: 2000 } }]),
  ).toBe("La pregunta es demasiado larga: máximo 2000 caracteres.");
  expect(describeValidation([{ loc: ["body", "port"], type: "greater_than_equal", ctx: { ge: 1024 } }])).toBe("El puerto debe ser al menos 1024.");
  expect(describeValidation([{ loc: ["body", "note"], type: "string_too_short", ctx: { min_length: 1 } }])).toBe("La nota no puede estar vacía.");
  expect(describeValidation("raro")).toBe("Hay datos que no son válidos.");
});
