"""CaseForm: editor guiado del caso — sin tocar JSON.

Fuente de verdad: `self._state` (dict con el formato de casefile) salvo la acción
actualmente seleccionada, cuyas filas viven en el DOM y se recogen al validar,
cambiar de acción o serializar. La utilidad se edita junto a cada resultado; como
en el modelo es global por etiqueta, una misma etiqueta con utilidades distintas
en dos acciones es un error señalado en vivo.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Select, Static

from oraqlo.casefile import CaseError, TEMPLATE, parse_case


class CaseForm(Vertical):
    DEFAULT_CSS = """
    CaseForm .cf-row { height: auto; }
    CaseForm .cf-row Label { padding: 1 1 0 0; width: 14; text-align: right; }
    CaseForm .cf-row Input { width: 1fr; }
    CaseForm .cf-short Input { width: 10; }
    CaseForm #cf-outcomes { height: auto; max-height: 12; border: round $surface; }
    CaseForm .cf-outcome-row Input.cf-label { width: 2fr; }
    CaseForm .cf-outcome-row Input.cf-num { width: 10; }
    CaseForm .cf-outcome-row Button { min-width: 5; }
    CaseForm #cf-status { height: auto; min-height: 2; padding: 0 1; }
    CaseForm #cf-action-select { width: 30; }
    """

    def __init__(self, data: dict | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state: dict = data or {k: v for k, v in TEMPLATE.items()}
        self._current_action: str | None = next(iter(self._state.get("actions", {})), None)
        # Referencias directas a los widgets de cada fila de resultado: el DOM
        # monta de forma asíncrona y consultarlo justo tras mount() no es fiable.
        self._rows: list[tuple[Horizontal, Input, Input, Input]] = []

    # ── Layout ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        risk = self._state.get("risk", {})
        with Horizontal(classes="cf-row"):
            yield Label("Pregunta:")
            yield Input(value=str(self._state.get("question", "")), id="cf-question")
        with Horizontal(classes="cf-row cf-short"):
            yield Label("Horizonte días:")
            yield Input(value=str(self._state.get("horizon_days", 180)), id="cf-horizon", type="number")
            yield Label("Aversión riesgo:")
            yield Input(value=str(risk.get("risk_aversion", 0.3)), id="cf-aversion", type="number")
            yield Label("Umbral ruina:")
            yield Input(value=str(risk.get("ruin_threshold", "")), id="cf-ruin", type="number")
            yield Label("P(ruina) máx:")
            yield Input(value=str(risk.get("max_ruin_probability", 0.01)), id="cf-maxruin", type="number")
        with Horizontal(classes="cf-row"):
            yield Label("Acción:")
            yield Select([], allow_blank=True, id="cf-action-select")
            yield Button("+ Acción", id="cf-add-action")
            yield Button("− Acción", id="cf-del-action")
        with Horizontal(classes="cf-row"):
            yield Label("Id:")
            yield Input(value="", id="cf-action-id")
            yield Label("Descripción:")
            yield Input(value="", id="cf-action-desc")
        with Horizontal(classes="cf-row"):
            yield Label("Resultados:")
            yield Static("[dim]nombre · probabilidad · utilidad[/dim]")
            yield Button("+ Resultado", id="cf-add-outcome")
        yield VerticalScroll(id="cf-outcomes")
        yield Static("", id="cf-status")

    def on_mount(self) -> None:
        self._refresh_action_select()
        if self._current_action:
            self._load_action(self._current_action)
        self._recompute_status()

    # ── Estado ↔ DOM ──────────────────────────────────────────────────────

    def _outcome_rows(self) -> list[tuple[str, str, str]]:
        """(etiqueta, prob, utilidad) como strings, leídos de las filas vivas."""
        return [
            (label.value.strip(), prob.value.strip(), utility.value.strip())
            for _, label, prob, utility in self._rows
        ]

    def _collect_current_action(self) -> None:
        """Vuelca la acción visible (DOM) al estado."""
        if self._current_action is None:
            return
        actions = self._state.setdefault("actions", {})
        entry = actions.setdefault(self._current_action, {})
        entry["description"] = self.query_one("#cf-action-desc", Input).value
        entry["_rows"] = self._outcome_rows()  # crudo; se convierte al serializar

    def _mount_outcome_row(self, label: str = "", prob: str = "", utility: str = "") -> None:
        label_input = Input(value=label, placeholder="resultado", classes="cf-label")
        prob_input = Input(value=prob, placeholder="prob", classes="cf-num", type="number")
        utility_input = Input(value=utility, placeholder="util", classes="cf-num", type="number")
        row = Horizontal(
            label_input, prob_input, utility_input,
            Button("−", classes="cf-del-outcome"),
            classes="cf-outcome-row",
        )
        self._rows.append((row, label_input, prob_input, utility_input))
        self.query_one("#cf-outcomes", VerticalScroll).mount(row)

    def _load_action(self, action_id: str) -> None:
        """Pinta una acción del estado en el DOM."""
        self._current_action = action_id
        entry = self._state.get("actions", {}).get(action_id, {})
        self.query_one("#cf-action-id", Input).value = action_id
        self.query_one("#cf-action-desc", Input).value = str(entry.get("description", ""))
        self._rows.clear()
        container = self.query_one("#cf-outcomes", VerticalScroll)
        container.remove_children()
        rows = entry.get("_rows")
        if rows is None:  # viene de un dict externo: combinar outcomes + utilities
            utilities = self._state.get("utilities", {})
            rows = [
                (label, str(p), str(utilities.get(label, "")))
                for label, p in entry.get("outcomes", {}).items()
            ]
        for label, prob, utility in rows:
            self._mount_outcome_row(str(label), str(prob), str(utility))

    def _refresh_action_select(self) -> None:
        select = self.query_one("#cf-action-select", Select)
        options = [(aid, aid) for aid in self._state.get("actions", {})]
        select.set_options(options)
        if self._current_action in self._state.get("actions", {}):
            select.value = self._current_action

    # ── Serialización ─────────────────────────────────────────────────────

    @staticmethod
    def _num(raw: str, field: str) -> float:
        try:
            return float(raw)
        except ValueError as e:
            raise CaseError(f"'{raw or '(vacío)'}' no es un número válido en {field}.") from e

    def to_dict(self) -> dict:
        """Serializa el formulario al formato de archivo de caso.

        Lanza CaseError con mensaje accionable si algún campo no es convertible
        o una etiqueta tiene utilidades contradictorias entre acciones.
        """
        self._collect_current_action()
        actions: dict = {}
        utilities: dict[str, float] = {}
        for action_id, entry in self._state.get("actions", {}).items():
            rows = entry.get("_rows")
            if rows is None:
                rows = [
                    (label, str(p), str(self._state.get("utilities", {}).get(label, "")))
                    for label, p in entry.get("outcomes", {}).items()
                ]
            outcomes: dict[str, float] = {}
            for label, prob, utility in rows:
                if not label:
                    raise CaseError(f'Hay un resultado sin nombre en la acción "{action_id}".')
                outcomes[label] = self._num(prob, f'probabilidad de "{label}" ({action_id})')
                value = self._num(utility, f'utilidad de "{label}"')
                if label in utilities and abs(utilities[label] - value) > 1e-9:
                    raise CaseError(
                        f'La etiqueta "{label}" tiene utilidades contradictorias '
                        f"({utilities[label]} y {value}); debe ser la misma en todas las acciones."
                    )
                utilities[label] = value
            actions[action_id] = {
                "description": str(entry.get("description", "")),
                "outcomes": outcomes,
            }

        ruin_raw = self.query_one("#cf-ruin", Input).value.strip()
        return {
            "question": self.query_one("#cf-question", Input).value,
            "horizon_days": self._num(self.query_one("#cf-horizon", Input).value, "horizonte"),
            "actions": actions,
            "utilities": utilities,
            "risk": {
                "risk_aversion": self._num(self.query_one("#cf-aversion", Input).value, "aversión"),
                "ruin_threshold": self._num(ruin_raw, "umbral de ruina") if ruin_raw else None,
                "max_ruin_probability": self._num(
                    self.query_one("#cf-maxruin", Input).value, "P(ruina) máx"
                ),
            },
        }

    def load_dict(self, data: dict) -> None:
        """Carga un caso externo (p. ej. desde el modo JSON o un archivo)."""
        self._state = data
        self._current_action = next(iter(data.get("actions", {})), None)
        self.query_one("#cf-question", Input).value = str(data.get("question", ""))
        self.query_one("#cf-horizon", Input).value = str(data.get("horizon_days", 180))
        risk = data.get("risk", {})
        self.query_one("#cf-aversion", Input).value = str(risk.get("risk_aversion", 0.3))
        ruin = risk.get("ruin_threshold")
        self.query_one("#cf-ruin", Input).value = "" if ruin is None else str(ruin)
        self.query_one("#cf-maxruin", Input).value = str(risk.get("max_ruin_probability", 0.01))
        self._refresh_action_select()
        if self._current_action:
            self._load_action(self._current_action)
        else:
            self._rows.clear()
            self.query_one("#cf-outcomes", VerticalScroll).remove_children()
        self._recompute_status()

    # ── Validación en vivo ────────────────────────────────────────────────

    def _recompute_status(self) -> None:
        status = self.query_one("#cf-status", Static)
        try:
            data = self.to_dict()
        except CaseError as e:
            status.update(f"[red]✗ {e}[/red]")
            return
        sums = []
        for action_id, entry in data["actions"].items():
            total = sum(entry["outcomes"].values())
            ok = 0.99 <= total <= 1.01
            color = "green" if ok else "red"
            sums.append(f"[{color}]{action_id}: Σp={total:.2f}[/{color}]")
        try:
            parse_case(data)
            status.update("[green]✓ Caso válido[/green] · " + "  ".join(sums))
        except CaseError as e:
            status.update(f"[red]✗ {e}[/red] · " + "  ".join(sums))

    # ── Eventos ───────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "cf-action-id":
            self._rename_current_action(event.value.strip())
        self._recompute_status()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "cf-action-select":
            return
        # El sentinel de "sin selección" varía entre versiones de Textual:
        # solo los valores string son ids de acción reales.
        if not isinstance(event.value, str) or event.value == self._current_action:
            return
        if event.value not in self._state.get("actions", {}):
            return
        self._collect_current_action()
        self._load_action(event.value)
        self._recompute_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cf-add-action":
            self._add_action()
        elif event.button.id == "cf-del-action":
            self._delete_current_action()
        elif event.button.id == "cf-add-outcome":
            self._mount_outcome_row()
        elif event.button.has_class("cf-del-outcome"):
            row = event.button.parent
            self._rows = [entry for entry in self._rows if entry[0] is not row]
            row.remove()
            self._recompute_status()
        event.stop()

    def _add_action(self) -> None:
        self._collect_current_action()
        actions = self._state.setdefault("actions", {})
        n = 1
        while f"accion-{n}" in actions:
            n += 1
        new_id = f"accion-{n}"
        actions[new_id] = {"description": "", "_rows": []}
        self._refresh_action_select()
        self.query_one("#cf-action-select", Select).value = new_id
        self._load_action(new_id)
        self._recompute_status()

    def _delete_current_action(self) -> None:
        actions = self._state.get("actions", {})
        if self._current_action is None or len(actions) <= 1:
            self.query_one("#cf-status", Static).update(
                "[red]✗ Un caso necesita al menos una acción.[/red]"
            )
            return
        actions.pop(self._current_action, None)
        self._current_action = next(iter(actions))
        self._refresh_action_select()
        self._load_action(self._current_action)
        self._recompute_status()

    def _rename_current_action(self, new_id: str) -> None:
        if not new_id or self._current_action is None or new_id == self._current_action:
            return
        actions = self._state.get("actions", {})
        if new_id in actions:
            return  # duplicado: se señalará al validar si queda inconsistente
        self._collect_current_action()
        actions[new_id] = actions.pop(self._current_action)
        self._current_action = new_id
        self._refresh_action_select()
