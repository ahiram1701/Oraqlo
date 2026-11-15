import { render, screen } from "@testing-library/react";
import { ProbBars } from "./ProbBars";

test("ordena de más a menos probable y muestra porcentajes", () => {
  render(<ProbBars outcomes={{ baja: 0.25, sube: 0.75 }} />);
  const items = screen.getAllByRole("listitem");
  expect(items[0]).toHaveTextContent("sube");
  expect(items[0]).toHaveTextContent("75%");
  expect(items[1]).toHaveTextContent("25%");
});
