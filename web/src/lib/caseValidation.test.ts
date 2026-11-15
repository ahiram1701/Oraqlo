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
