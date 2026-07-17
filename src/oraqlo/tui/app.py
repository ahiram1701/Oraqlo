"""TUI de Oraqlo: preguntar, decidir, lograr objetivos y aprender de los resultados.

Pestañas:
- Preguntar: escribe tu pregunta (previsión rápida / análisis completo / lograr
             objetivo), con investigación web opcional.
- Caso:      formulario guiado (pregunta, acciones, resultados, riesgo) con validación
             en vivo; modo JSON avanzado conmutable.
- Ejecutar:  solo previsión o ciclo completo sobre el caso. Debate del panel en vivo,
             duración por fase, informe exportable.
- Objetivos: seguimiento de los planes: hitos, check-ins con re-estimación de P,
             re-planificación y cierre que resuelve las predicciones.
- Ledger:    predicciones abiertas/resueltas persistidas; resolver sin teclear,
             eliminar con confirmación, fiabilidad por fuente.
- Práctica:  auto-mejora autónoma con Polymarket (solo lectura) y benchmark contra
             el mercado.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    LoadingIndicator,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
    TextArea,
)
from textual.worker import Worker, WorkerState

from oraqlo.autopractice import MARKET_SOURCE, PracticeEngine
from oraqlo.casefile import CaseError, CaseSpec, case_to_dict, parse_case, template_json
from oraqlo.polymarket import PolymarketClient
from oraqlo.drafter import DraftError, draft_case
from oraqlo.forecaster.llm import ForecastParseError, LLMForecaster
from oraqlo.goals import GoalError, GoalStrategist
from oraqlo.goaltrack import GoalStore, GoalTracker, GoalTrackError
from oraqlo.llm.ollama import OllamaBackendConfig, OllamaProvider, OllamaUnavailableError
from oraqlo.llm.websearch import OllamaWebClient
from oraqlo.memory.store import JsonLedger
from oraqlo.panel import ROLE_PROMPTS, PanelError, RoleConfig, SequentialPanel
from oraqlo.report import render_goal_report_markdown, render_report_markdown, slugify
from oraqlo.research import Researcher, ResearchBrief, ResearchError
from oraqlo.simulator.base import TreeSearchEngine
from oraqlo.strategy.planner import ExpectedUtilityPlanner
from oraqlo.tui.case_form import CaseForm
from oraqlo.world_model import WorldModel, WorldState

ROLE_TEMPERATURES = {"estratega": 0.7, "red_team": 0.9, "oraculo": 0.2, "juez": 0.1}
DEFAULT_MODELS = {"cloud": "gpt-oss:120b-cloud", "local": "llama3.1"}
OTHER_OUTCOME = "__other__"


class ConfirmScreen(ModalScreen[bool]):
    """Confirmación modal para acciones destructivas: Eliminar / Cancelar."""

    CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Vertical {
        width: 60; height: auto; padding: 1 2;
        border: thick $error; background: $surface;
    }
    ConfirmScreen Static { margin-bottom: 1; }
    ConfirmScreen Button { margin-right: 2; }
    """

    def __init__(self, message: str, confirm_label: str = "Eliminar") -> None:
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.message)
            with Horizontal():
                yield Button(self.confirm_label, variant="error", id="confirm-yes")
                yield Button("Cancelar", variant="primary", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")


class OraqloTUI(App):
    TITLE = "Oraqlo"
    SUB_TITLE = "estratega bajo incertidumbre"

    CSS = """
    TabbedContent { height: 1fr; }
    .controls { height: auto; padding: 0 1; }
    .controls Button { margin: 0 1 0 0; }
    .controls Input { width: 1fr; }
    .controls Select { width: 32; }
    .controls Label { padding: 1 1 0 0; }
    #case-editor { height: 1fr; border: round $primary; }
    #case-status { height: auto; min-height: 1; padding: 0 1; color: $text-muted; }
    #log, #ask-log, #practice-log { height: 1fr; border: round $primary; }
    #goals-table { height: auto; max-height: 8; border: round $primary; min-height: 3; }
    #milestones-table { height: auto; max-height: 8; border: round $surface; min-height: 3; }
    #goals-log { height: 1fr; border: round $surface; min-height: 4; }
    #tab-goals Label { padding: 0 1; }
    #practice-interval { width: 10; }
    #practice-status { height: auto; min-height: 2; padding: 0 1; color: $text-muted; }
    #ask-horizon { width: 10; }
    #busy { width: 8; height: 1; display: none; }
    #open-table { height: 1fr; border: round $primary; min-height: 5; }
    #resolved-table { height: 1fr; border: round $surface; min-height: 4; }
    #tab-ledger Label { padding: 0 1; }
    #forecast-detail { height: auto; min-height: 2; padding: 0 1; color: $text-muted; }
    #reliability { height: auto; min-height: 2; padding: 0 1; color: $text-muted; }
    #actual { display: none; }
    """

    BINDINGS = [("ctrl+q", "quit", "Salir")]

    def __init__(
        self,
        case_path: str | Path = "cases/mi_caso.json",
        ledger_path: str | Path = "oraqlo_ledger.json",
        goals_path: str | Path = "oraqlo_goals.json",
        settings_path: str | Path = "oraqlo_settings.json",
    ) -> None:
        super().__init__()
        self.case_path = Path(case_path)
        self.ledger = JsonLedger(ledger_path)
        self.goal_store = GoalStore(goals_path)
        self.settings_path = Path(settings_path)
        self._json_mode = False
        self._last_run: dict | None = None
        self._practice_timer = None
        # One-shot: al restaurar el switch guardado NO se lanza pasada inmediata
        # (abrir la TUI no debe costar llamadas LLM por sí solo).
        self._restoring_practice = False

    # ── Layout ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Preguntar", id="tab-ask"):
                with Vertical():
                    with Horizontal(classes="controls"):
                        yield Label("Pregunta:")
                        yield Input(
                            placeholder="Escribe tu pregunta estratégica y pulsa Enter…",
                            id="ask-question",
                        )
                    with Horizontal(classes="controls"):
                        yield Label("Horizonte días:")
                        yield Input(value="180", id="ask-horizon", type="number")
                        yield Label("Investigar en internet:")
                        yield Switch(value=True, id="ask-research")
                        yield Button("Previsión rápida", id="btn-ask-forecast")
                        yield Button("Análisis completo", id="btn-ask-full", variant="primary")
                        yield Button("Lograr objetivo", id="btn-ask-goal", variant="success")
                    yield RichLog(id="ask-log", wrap=True, markup=True)
            with TabPane("Caso", id="tab-caso"):
                with Vertical():
                    with Horizontal(classes="controls"):
                        yield Input(value=str(self.case_path), id="case-path")
                        yield Button("Cargar", id="btn-load")
                        yield Button("Guardar", id="btn-save")
                        yield Button("Modo JSON", id="btn-mode")
                    yield CaseForm(self._initial_case_dict(), id="case-form")
                    editor = TextArea("", id="case-editor")
                    editor.display = False
                    yield editor
                    yield Static("", id="case-status")
            with TabPane("Ejecutar", id="tab-run"):
                with Vertical():
                    with Horizontal(classes="controls"):
                        yield Label("Backend:")
                        yield Select(
                            [("cloud (ollama.com)", "cloud"), ("local (localhost)", "local")],
                            value="cloud",
                            allow_blank=False,
                            id="backend",
                        )
                        yield Select(
                            [(DEFAULT_MODELS["cloud"], DEFAULT_MODELS["cloud"])],
                            value=DEFAULT_MODELS["cloud"],
                            allow_blank=False,
                            id="model-select",
                        )
                        yield Button("↻ Modelos", id="btn-models")
                        yield Button("Solo previsión", id="btn-forecast")
                        yield Button("Ciclo completo", id="btn-cycle", variant="primary")
                        yield Button("Exportar informe", id="btn-export", disabled=True)
                        yield LoadingIndicator(id="busy")
                    yield RichLog(id="log", wrap=True, markup=True)
            with TabPane("Ledger", id="tab-ledger"):
                with Vertical():
                    yield Label("Predicciones abiertas:")
                    yield DataTable(id="open-table", cursor_type="row")
                    yield Static("", id="forecast-detail")
                    with Horizontal(classes="controls"):
                        yield Label("Resultado real:")
                        yield Select([], allow_blank=True, id="actual-select")
                        yield Input(placeholder="describe el resultado no contemplado", id="actual")
                        yield Button("Resolver", id="btn-resolve", variant="primary")
                        yield Button("Eliminar", id="btn-delete-open", variant="error")
                        yield Button("Refrescar", id="btn-refresh")
                    yield Label("Resueltas (historial de calibración):")
                    yield DataTable(id="resolved-table", cursor_type="row")
                    with Horizontal(classes="controls"):
                        yield Button("Eliminar resuelta", id="btn-delete-resolved", variant="error")
                        yield Static("", id="reliability")
            with TabPane("Objetivos", id="tab-goals"):
                with Vertical():
                    yield Label("Objetivos en seguimiento:")
                    yield DataTable(id="goals-table", cursor_type="row")
                    yield Label("Hitos del objetivo seleccionado:")
                    yield DataTable(id="milestones-table", cursor_type="row")
                    with Horizontal(classes="controls"):
                        yield Input(placeholder="nota de avance (¿qué ha pasado desde el último check-in?)",
                                    id="goal-note")
                        yield Button("Check-in", id="btn-goal-checkin", variant="primary")
                        yield Button("Hito cumplido", id="btn-milestone-done", variant="success")
                        yield Button("Hito fallado", id="btn-milestone-failed", variant="warning")
                    with Horizontal(classes="controls"):
                        yield Button("Re-planificar", id="btn-goal-replan")
                        yield Button("Cerrar: logrado", id="btn-goal-won", variant="success")
                        yield Button("Cerrar: no logrado", id="btn-goal-lost", variant="error")
                        yield Button("Eliminar objetivo", id="btn-goal-delete", variant="error")
                        yield Button("Refrescar", id="btn-goal-refresh")
                    yield RichLog(id="goals-log", wrap=True, markup=True)
            with TabPane("Práctica", id="tab-practice"):
                with Vertical():
                    with Horizontal(classes="controls"):
                        yield Label("Cada (min):")
                        yield Input(value="60", id="practice-interval", type="number")
                        yield Label("Autónoma:")
                        yield Switch(value=False, id="practice-auto")
                        yield Button("Pasada manual", id="btn-practice-run", variant="primary")
                    yield Static(
                        "Práctica con Polymarket (solo lectura, sin apuestas): Oraqlo predice "
                        "mercados reales, se compara con el precio de la multitud, resuelve "
                        "solo al cerrar cada mercado y usa su historial para recalibrarse.",
                        id="practice-status",
                    )
                    yield RichLog(id="practice-log", wrap=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#open-table", DataTable)
        table.add_columns("id", "fuente", "pregunta", "escenario más probable",
                          "creada", "vence")
        resolved = self.query_one("#resolved-table", DataTable)
        resolved.add_columns("id", "pregunta", "resultado real", "Brier", "resuelta el")
        goals = self.query_one("#goals-table", DataTable)
        goals.add_columns("objetivo", "P(objetivo)", "próximo hito", "fecha", "días", "estado")
        milestones = self.query_one("#milestones-table", DataTable)
        milestones.add_columns("hito", "fecha objetivo", "señal", "estado")
        self._refresh_ledger()
        self._refresh_goals()
        self._restore_practice_settings()
        overdue = GoalTracker(self.goal_store, self.ledger).overdue_milestones()
        if overdue:
            names = ", ".join(sorted({g.goal[:40] for g, _ in overdue}))
            self.notify(f"{len(overdue)} hito(s) vencido(s) sin marcar en: {names}",
                        title="Objetivos", severity="warning")

    def _initial_case_dict(self) -> dict:
        if self.case_path.exists():
            try:
                return json.loads(self.case_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass  # el archivo se podrá arreglar en modo JSON; arrancamos con plantilla
        return json.loads(template_json())

    # ── Helpers ───────────────────────────────────────────────────────────

    def _log(self, message: str, target: str = "#log") -> None:
        self.query_one(target, RichLog).write(message)

    def _wlog(self, message: str, target: str = "#log") -> None:
        self.call_from_thread(self._log, message, target)

    def _status(self, message: str) -> None:
        self.query_one("#case-status", Static).update(message)

    def _current_case(self) -> CaseSpec:
        if self._json_mode:
            text = self.query_one("#case-editor", TextArea).text
            try:
                return parse_case(json.loads(text))
            except json.JSONDecodeError as e:
                raise CaseError(f"JSON inválido: {e}") from e
        return parse_case(self.query_one("#case-form", CaseForm).to_dict())

    def _provider_and_model(self) -> tuple[OllamaProvider, str, str]:
        backend = str(self.query_one("#backend", Select).value)
        model = str(self.query_one("#model-select", Select).value)
        config = OllamaBackendConfig.cloud() if backend == "cloud" else OllamaBackendConfig.local()
        return OllamaProvider(config), model, backend

    def _set_busy(self, busy: bool) -> None:
        self.query_one("#busy", LoadingIndicator).display = busy
        for button_id in ("#btn-forecast", "#btn-cycle", "#btn-models",
                          "#btn-ask-forecast", "#btn-ask-full", "#btn-ask-goal"):
            self.query_one(button_id, Button).disabled = busy
        self.query_one("#btn-export", Button).disabled = busy or self._last_run is None

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "llm":
            return
        self._set_busy(event.state in (WorkerState.PENDING, WorkerState.RUNNING))

    def _refresh_ledger(self) -> None:
        table = self.query_one("#open-table", DataTable)
        table.clear()
        for f in self.ledger.open_forecasts:
            top = max(f.distribution.outcomes, key=f.distribution.outcomes.get)
            question = f.question if len(f.question) <= 60 else f.question[:57] + "..."
            if f.created_at is not None:
                created = f.created_at.strftime("%Y-%m-%d %H:%M")
                due = (f.created_at + f.horizon).strftime("%Y-%m-%d")
            else:
                created = due = "—"
            table.add_row(f.id[:8], f.source, question, top, created, due, key=f.id)
        resolved_table = self.query_one("#resolved-table", DataTable)
        resolved_table.clear()
        for r in self.ledger.resolved_forecasts:
            question = (r.forecast.question if len(r.forecast.question) <= 50
                        else r.forecast.question[:47] + "...")
            resolved_table.add_row(
                r.forecast.id[:8], question, r.actual_outcome, f"{r.brier:.3f}",
                r.resolved_at.strftime("%Y-%m-%d"), key=r.forecast.id,
            )
        sources = sorted({r.forecast.source for r in self.ledger.resolved_forecasts})
        if sources:
            lines = ["Fiabilidad por fuente (Brier medio; menor = mejor):"]
            lines += [f"  {s}: {self.ledger.reliability(s):.4f}" for s in sources]
        else:
            lines = ["Sin predicciones resueltas todavía: resuelve alguna para medir fiabilidad."]
        self.query_one("#reliability", Static).update("\n".join(lines))
        if table.row_count:
            self._show_forecast_detail(
                str(table.coordinate_to_cell_key(Coordinate(0, 0)).row_key.value)
            )
        else:
            self.query_one("#forecast-detail", Static).update("No hay predicciones abiertas.")
            self.query_one("#actual-select", Select).set_options([])

    def _show_forecast_detail(self, forecast_id: str) -> None:
        forecast = next((f for f in self.ledger.open_forecasts if f.id == forecast_id), None)
        if forecast is None:
            return
        dist = " · ".join(
            f"{label} {p:.0%}"
            for label, p in sorted(forecast.distribution.outcomes.items(), key=lambda kv: -kv[1])
        )
        detail = [f"[b]{forecast.question}[/b]", dist]
        if forecast.assumptions:
            detail.append("[dim]Supuestos: " + " | ".join(forecast.assumptions[:3]) + "[/dim]")
        self.query_one("#forecast-detail", Static).update("\n".join(detail))
        options = [(label, label) for label in forecast.distribution.outcomes]
        options.append(("otro resultado…", OTHER_OUTCOME))
        select = self.query_one("#actual-select", Select)
        select.set_options(options)
        select.value = max(
            forecast.distribution.outcomes, key=forecast.distribution.outcomes.get
        )
        self.query_one("#actual", Input).display = False

    # ── Eventos ───────────────────────────────────────────────────────────

    def on_select_changed(self, event: Select.Changed) -> None:
        if not isinstance(event.value, str):  # sentinel de "sin selección"
            return
        if event.select.id == "backend":
            default = DEFAULT_MODELS[event.value]
            model_select = self.query_one("#model-select", Select)
            model_select.set_options([(default, default)])
            model_select.value = default
        elif event.select.id == "actual-select":
            self.query_one("#actual", Input).display = event.value == OTHER_OUTCOME

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        if event.data_table.id == "open-table":
            self._show_forecast_detail(str(event.row_key.value))
        elif event.data_table.id == "goals-table":
            self._show_goal_detail(str(event.row_key.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "btn-load": self._handle_load,
            "btn-save": self._handle_save,
            "btn-mode": self._handle_mode_toggle,
            "btn-models": self._handle_models,
            "btn-forecast": self._handle_forecast,
            "btn-cycle": self._handle_cycle,
            "btn-export": self._handle_export,
            "btn-resolve": self._handle_resolve,
            "btn-goal-checkin": self._handle_goal_checkin,
            "btn-milestone-done": lambda: self._handle_milestone("cumplido"),
            "btn-milestone-failed": lambda: self._handle_milestone("fallado"),
            "btn-goal-replan": self._handle_goal_replan,
            "btn-goal-won": lambda: self._handle_goal_close(True),
            "btn-goal-lost": lambda: self._handle_goal_close(False),
            "btn-goal-delete": self._handle_goal_delete,
            "btn-goal-refresh": self._refresh_goals,
            "btn-practice-run": self._handle_practice_run,
            "btn-delete-open": self._handle_delete_open,
            "btn-delete-resolved": self._handle_delete_resolved,
            "btn-refresh": self._refresh_ledger,
            "btn-ask-forecast": lambda: self._handle_ask(full=False),
            "btn-ask-full": lambda: self._handle_ask(full=True),
            "btn-ask-goal": self._handle_goal,
        }
        handler = handlers.get(event.button.id)
        if handler:
            handler()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter en la pregunta = previsión rápida: el camino más corto de todos.
        if event.input.id == "ask-question":
            self._handle_ask(full=False)

    def _ask_inputs(self) -> tuple[str, int] | None:
        question = self.query_one("#ask-question", Input).value.strip()
        if not question:
            self.notify("Escribe tu pregunta primero.", severity="warning")
            return None
        try:
            horizon_days = int(float(self.query_one("#ask-horizon", Input).value or 180))
        except ValueError:
            horizon_days = 180
        return question, horizon_days

    def _handle_ask(self, full: bool) -> None:
        inputs = self._ask_inputs()
        if inputs is None:
            return
        provider, model, backend = self._provider_and_model()
        self._run_ask(inputs[0], inputs[1], full, provider, model, backend)

    def _handle_goal(self) -> None:
        inputs = self._ask_inputs()
        if inputs is None:
            return
        provider, model, backend = self._provider_and_model()
        self._run_goal(inputs[0], inputs[1], provider, model, backend)

    # ── Caso: formulario ↔ JSON, cargar/guardar ───────────────────────────

    def _handle_mode_toggle(self) -> None:
        form = self.query_one("#case-form", CaseForm)
        editor = self.query_one("#case-editor", TextArea)
        button = self.query_one("#btn-mode", Button)
        if not self._json_mode:
            try:
                data = form.to_dict()
            except CaseError as e:
                self._status(f"[!] Corrige el formulario antes de pasar a JSON: {e}")
                return
            editor.text = json.dumps(data, ensure_ascii=False, indent=2)
            form.display, editor.display = False, True
            button.label = "Modo formulario"
            self._json_mode = True
            self._status("Modo JSON: edición avanzada.")
        else:
            try:
                data = json.loads(editor.text)
                parse_case(data)
            except (json.JSONDecodeError, CaseError) as e:
                self._status(f"[!] El JSON debe ser un caso válido para volver al formulario: {e}")
                return
            form.load_dict(data)
            form.display, editor.display = True, False
            button.label = "Modo JSON"
            self._json_mode = False
            self._status("Modo formulario.")

    def _handle_load(self) -> None:
        path = Path(self.query_one("#case-path", Input).value)
        if not path.exists():
            self._status(f"[!] No existe {path}")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            self._status(f"[!] {path} no es JSON válido: {e}")
            return
        self.query_one("#case-editor", TextArea).text = json.dumps(data, ensure_ascii=False, indent=2)
        if not self._json_mode:
            try:
                parse_case(data)
                self.query_one("#case-form", CaseForm).load_dict(data)
            except CaseError as e:
                self._status(f"[!] Caso inválido; ábrelo en Modo JSON para corregirlo: {e}")
                return
        self._status(f"Cargado {path}")

    def _handle_save(self) -> None:
        path = Path(self.query_one("#case-path", Input).value)
        if self._json_mode:
            text = self.query_one("#case-editor", TextArea).text
        else:
            try:
                data = self.query_one("#case-form", CaseForm).to_dict()
            except CaseError as e:
                self._status(f"[!] Corrige el formulario antes de guardar: {e}")
                return
            text = json.dumps(data, ensure_ascii=False, indent=2)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self._status(f"Guardado en {path}")

    # ── Ejecutar ──────────────────────────────────────────────────────────

    def _case_or_log(self) -> CaseSpec | None:
        try:
            return self._current_case()
        except CaseError as e:
            self._log(f"[red]Caso inválido:[/red] {e}")
            self.notify(str(e), title="Caso inválido", severity="error")
            return None

    def _handle_models(self) -> None:
        provider, _, backend = self._provider_and_model()
        self._fetch_models(provider, backend)

    def _handle_forecast(self) -> None:
        spec = self._case_or_log()
        if spec:
            provider, model, backend = self._provider_and_model()
            self._run_forecast(spec, provider, model, backend)

    def _handle_cycle(self) -> None:
        spec = self._case_or_log()
        if spec:
            provider, model, backend = self._provider_and_model()
            self._run_cycle(spec, provider, model, backend)

    def _handle_export(self) -> None:
        if self._last_run is None:
            return
        run = self._last_run
        brief = run.get("brief")
        facts = [(f.text, f.source_url) for f in brief.facts] if brief else None
        if run.get("goal_plan") is not None:
            tracked = run.get("tracked")
            if tracked is not None:  # estado fresco del store (check-ins posteriores)
                try:
                    tracked = self.goal_store.get(tracked.id)
                except KeyError:
                    pass
            markdown = render_goal_report_markdown(
                run["goal_plan"], model=run["model"], backend=run["backend"],
                research_facts=facts, tracked=tracked,
            )
        else:
            markdown = render_report_markdown(
                question=run["question"], forecast=run["forecast"], ranking=run["ranking"],
                verdict=run.get("verdict"), model=run["model"], backend=run["backend"],
                research_facts=facts,
            )
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        path = Path("informes") / f"{stamp}-{slugify(run['question'])}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        self._log(f"Informe exportado: [b]{path}[/b]")
        self.notify(str(path), title="Informe exportado")

    def _handle_resolve(self) -> None:
        table = self.query_one("#open-table", DataTable)
        if table.row_count == 0:
            self.notify("No hay predicciones abiertas.", severity="warning")
            return
        selected = self.query_one("#actual-select", Select).value
        if not isinstance(selected, str):  # sentinel de "sin selección"
            self.notify("Elige el resultado observado.", severity="warning")
            return
        actual = selected
        if actual == OTHER_OUTCOME:
            actual = self.query_one("#actual", Input).value.strip()
            if not actual:
                self.notify("Describe el resultado no contemplado.", severity="warning")
                return
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        forecast_id = str(row_key.value)
        try:
            resolved = self.ledger.resolve(forecast_id, actual)
        except KeyError as e:
            self.notify(str(e), severity="error")
            return
        self.notify(
            f"Brier {resolved.brier:.4f} (0 = perfecto, 2 = certeza equivocada)",
            title=f"Resuelta {forecast_id[:8]}",
        )
        self._refresh_ledger()

    # ── Objetivos: seguimiento ────────────────────────────────────────────

    def _refresh_goals(self) -> None:
        table = self.query_one("#goals-table", DataTable)
        table.clear()
        goals = sorted(self.goal_store.list_all(),
                       key=lambda g: (g.status != "activo", g.deadline))
        for g in goals:
            trajectory = g.trajectory
            trend = ""
            if len(trajectory) >= 2:
                trend = " ↗" if trajectory[-1] > trajectory[-2] else (
                    " ↘" if trajectory[-1] < trajectory[-2] else " →")
            nxt = g.next_milestone
            nxt_text = (nxt.text[:30] + ("…" if len(nxt.text) > 30 else "")) if nxt else "—"
            nxt_date = (f"{nxt.target_date} ⚠ VENCIDO" if nxt and nxt.overdue
                        else (nxt.target_date if nxt else "—"))
            table.add_row(
                g.goal[:45] + ("…" if len(g.goal) > 45 else ""),
                f"{g.current_p:.0%}{trend}", nxt_text, nxt_date,
                str(g.days_left), g.status, key=g.id,
            )
        if table.row_count:
            self._show_goal_detail(str(
                table.coordinate_to_cell_key(Coordinate(0, 0)).row_key.value
            ))
        else:
            self.query_one("#milestones-table", DataTable).clear()

    def _show_goal_detail(self, goal_id: str) -> None:
        try:
            g = self.goal_store.get(goal_id)
        except KeyError:
            return
        table = self.query_one("#milestones-table", DataTable)
        table.clear()
        for i, m in enumerate(g.milestones):
            status = f"{m.status} ⚠ VENCIDO" if m.overdue else m.status
            table.add_row(m.text[:50], m.target_date, m.signal[:40], status, key=str(i))
        log = self.query_one("#goals-log", RichLog)
        log.clear()
        log.write(f"[b]{g.goal}[/b]  (plan v{g.plan_version}, límite {g.deadline})")
        log.write("Trayectoria P(objetivo): " + " → ".join(f"{p:.0%}" for p in g.trajectory)
                  + f"  [dim](línea base sin plan: {g.baseline_p:.0%})[/dim]")
        for c in g.checkins[-5:]:
            log.write(f"  {c.date[:10]} · {c.p_estimate:.0%} · {c.note}")
            if c.comment:
                log.write(f"    [dim]{c.comment}[/dim]")

    def _selected_goal(self) -> str | None:
        goal_id = self._selected_row_key(self.query_one("#goals-table", DataTable))
        if goal_id is None:
            self.notify("No hay objetivos en seguimiento.", severity="warning")
        return goal_id

    def _handle_goal_checkin(self) -> None:
        goal_id = self._selected_goal()
        if goal_id is None:
            return
        note = self.query_one("#goal-note", Input).value.strip()
        if not note:
            self.notify("Escribe la nota de avance antes del check-in.", severity="warning")
            return
        provider, model, backend = self._provider_and_model()
        self._run_checkin(goal_id, note, provider, model)

    @work(thread=True, exclusive=True, group="llm")
    def _run_checkin(self, goal_id: str, note: str, provider: OllamaProvider, model: str) -> None:
        log = lambda msg: self._wlog(msg, "#goals-log")
        if not provider.is_available():
            log("[red]Backend no disponible.[/red]")
            return
        tracker = GoalTracker(self.goal_store, self.ledger, provider, model)
        try:
            checkin = tracker.check_in(goal_id, note)
        except (GoalTrackError, OllamaUnavailableError) as e:
            log(f"[red]Check-in fallido:[/red] {e}")
            return
        g = self.goal_store.get(goal_id)
        prev = g.trajectory[-2]
        arrow = "↗" if checkin.p_estimate > prev else ("↘" if checkin.p_estimate < prev else "→")
        log(f"[bold]Check-in:[/bold] P(objetivo) {prev:.0%} {arrow} {checkin.p_estimate:.0%}")
        log(f"  {checkin.comment}")
        if checkin.replan_recommended:
            log("[yellow]Recomendado re-planificar (hito fallado/vencido o trayectoria débil): "
                "botón 'Re-planificar'.[/yellow]")
        self.call_from_thread(setattr, self.query_one("#goal-note", Input), "value", "")
        self.call_from_thread(self._refresh_goals)
        self.call_from_thread(self.notify,
                              f"P: {prev:.0%} {arrow} {checkin.p_estimate:.0%}", title="Check-in")

    def _handle_milestone(self, status: str) -> None:
        goal_id = self._selected_goal()
        if goal_id is None:
            return
        index = self._selected_row_key(self.query_one("#milestones-table", DataTable))
        if index is None:
            self.notify("Selecciona un hito.", severity="warning")
            return
        tracker = GoalTracker(self.goal_store, self.ledger)
        try:
            milestone = tracker.set_milestone(goal_id, int(index), status)
        except GoalTrackError as e:
            self.notify(str(e), severity="error")
            return
        self.notify(f"'{milestone.text[:40]}' → {status}", title="Hito actualizado")
        self._refresh_goals()
        self._show_goal_detail(goal_id)

    def _handle_goal_replan(self) -> None:
        goal_id = self._selected_goal()
        if goal_id is None:
            return
        provider, model, backend = self._provider_and_model()
        self._run_replan(goal_id, provider, model)

    @work(thread=True, exclusive=True, group="llm")
    def _run_replan(self, goal_id: str, provider: OllamaProvider, model: str) -> None:
        log = lambda msg: self._wlog(msg, "#goals-log")
        if not provider.is_available():
            log("[red]Backend no disponible.[/red]")
            return
        log("[bold]Re-planificando con el estado actual…[/bold]")
        tracker = GoalTracker(self.goal_store, self.ledger, provider, model)
        try:
            g = tracker.replan(goal_id)
        except (GoalTrackError, OllamaUnavailableError) as e:
            log(f"[red]Re-planificación fallida:[/red] {e}")
            return
        log(f"[green]Plan v{g.plan_version}:[/green] {g.summary}")
        for step in g.steps:
            log(f"  {step.order}. {step.action} — [dim]{step.when}[/dim]")
        self.call_from_thread(self._refresh_goals)
        self.call_from_thread(self.notify, f"Plan v{g.plan_version} listo", title="Re-plan")

    def _handle_goal_close(self, achieved: bool) -> None:
        goal_id = self._selected_goal()
        if goal_id is None:
            return
        word = "LOGRADO" if achieved else "NO LOGRADO"

        def on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return
            tracker = GoalTracker(self.goal_store, self.ledger)
            warnings = tracker.close(goal_id, achieved)
            for w in warnings:
                self._log(f"  {w}", "#goals-log")
            self.notify(f"Objetivo cerrado como {word}; predicciones resueltas.",
                        title="Cierre")
            self._refresh_goals()
            self._refresh_ledger()

        self.push_screen(
            ConfirmScreen(
                f"¿Cerrar este objetivo como {word}?\n"
                "Se resolverán sus dos predicciones en el ledger (alimenta la calibración).",
                confirm_label="Cerrar",
            ),
            on_confirm,
        )

    def _handle_goal_delete(self) -> None:
        goal_id = self._selected_goal()
        if goal_id is None:
            return
        try:
            g = self.goal_store.get(goal_id)
        except KeyError:
            return

        def on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return
            tracker = GoalTracker(self.goal_store, self.ledger)
            warnings = tracker.delete(goal_id)
            for w in warnings:
                self._log(f"  {w}", "#goals-log")
            self.notify("Objetivo eliminado.", title="Objetivos")
            self._refresh_goals()
            self._refresh_ledger()

        self.push_screen(
            ConfirmScreen(
                f"¿Eliminar el objetivo '{g.goal[:40]}'?\n"
                "Sus predicciones abiertas se descartan (sin puntuar, no afecta a la\n"
                "calibración); las ya resueltas se conservan. No se puede deshacer."
            ),
            on_confirm,
        )

    # ── Práctica autónoma ─────────────────────────────────────────────────

    def _load_settings(self) -> dict:
        if not self.settings_path.exists():
            return {}
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_settings(self) -> None:
        try:
            minutes = float(self.query_one("#practice-interval", Input).value or 60)
        except ValueError:
            minutes = 60.0
        payload = {
            "practice_auto": self.query_one("#practice-auto", Switch).value,
            "practice_interval_min": minutes,
        }
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _restore_practice_settings(self) -> None:
        settings = self._load_settings()
        interval = settings.get("practice_interval_min")
        if interval is not None:
            self.query_one("#practice-interval", Input).value = f"{float(interval):g}"
        if settings.get("practice_auto"):
            self._restoring_practice = True  # el handler lo consume (sin pasada inmediata)
            self.query_one("#practice-auto", Switch).value = True

    def _practice_interval_min(self) -> float:
        try:
            return max(5.0, float(self.query_one("#practice-interval", Input).value or 60))
        except ValueError:
            return 60.0

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id != "practice-auto":
            return
        restoring, self._restoring_practice = self._restoring_practice, False
        if self._practice_timer is not None:
            self._practice_timer.stop()
            self._practice_timer = None
        if event.value:
            minutes = self._practice_interval_min()
            self._practice_timer = self.set_interval(minutes * 60, self._handle_practice_run)
            if restoring:
                self._log(f"[green]Práctica autónoma reanudada (guardada de la sesión "
                          f"anterior): cada {minutes:.0f} min; próxima pasada en "
                          f"{minutes:.0f} min.[/green]", "#practice-log")
            else:
                self._log(f"[green]Práctica autónoma activada: cada {minutes:.0f} min "
                          f"(mientras la TUI esté abierta). Primera pasada ahora.[/green]",
                          "#practice-log")
                self._handle_practice_run()
        else:
            self._log("[yellow]Práctica autónoma desactivada.[/yellow]", "#practice-log")
        self._save_settings()

    def on_input_changed(self, event: Input.Changed) -> None:
        # Persistir el intervalo al editarlo (se aplica al próximo encendido del switch
        # o al reanudar en la siguiente sesión).
        if event.input.id == "practice-interval":
            self._save_settings()

    def _handle_practice_run(self) -> None:
        provider, model, backend = self._provider_and_model()
        self._run_practice(provider, model, backend)

    @work(thread=True, exclusive=True, group="llm")
    def _run_practice(self, provider: OllamaProvider, model: str, backend: str) -> None:
        target = "#practice-log"
        log = lambda msg: self._wlog(msg, target)
        log(f"[bold]── Pasada de práctica ──[/bold] {model} @ {backend} · "
            f"{datetime.now().strftime('%H:%M')}")
        if not provider.is_available():
            log(f"[red]Backend no disponible[/red] ({provider.config.host}).")
            return
        web = OllamaWebClient()
        researcher = Researcher(web, provider, model) if web.is_available() else None
        engine = PracticeEngine(
            PolymarketClient(), provider, model, self.ledger,
            researcher=researcher, markets_per_run=2,
        )
        report = engine.run_once(
            research=self._research_enabled(),
            on_progress=lambda msg: log(f"  [cyan]{msg}[/cyan]"),
        )
        for error in report.errors:
            log(f"  [red]{error}[/red]")
        log(f"predichas: {len(report.predicted)} · resueltas: {len(report.resolved)}")

        status = ["Práctica con Polymarket (solo lectura, sin apuestas)."]
        if report.reliability_oraqlo is not None:
            n = len([r for r in self.ledger.resolved_forecasts
                     if r.forecast.source == engine.oraqlo_source])
            line = f"Brier Oraqlo: {report.reliability_oraqlo:.3f} en {n} resueltas"
            if report.reliability_market is not None:
                line += f" · mercado: {report.reliability_market:.3f}"
                line += " — vas MEJOR que la multitud" if (
                    report.reliability_oraqlo < report.reliability_market
                ) else " — la multitud aún te gana"
            status.append(line)
            log(f"[bold]{line}[/bold]")
        if report.advice:
            status.append(f"Consejo activo: {report.advice}")
        self.call_from_thread(
            self.query_one("#practice-status", Static).update, "\n".join(status)
        )
        self.call_from_thread(self._refresh_ledger)

    @staticmethod
    def _selected_row_key(table: DataTable) -> str | None:
        if table.row_count == 0:
            return None
        return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def _handle_delete_open(self) -> None:
        forecast_id = self._selected_row_key(self.query_one("#open-table", DataTable))
        if forecast_id is None:
            self.notify("No hay predicciones abiertas.", severity="warning")
            return

        def on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return
            try:
                self.ledger.discard(forecast_id)
            except KeyError as e:
                self.notify(str(e), severity="error")
                return
            self.notify("Descartada sin puntuar (no afecta a la calibración).",
                        title=f"Eliminada {forecast_id[:8]}")
            self._refresh_ledger()

        self.push_screen(
            ConfirmScreen(
                f"¿Eliminar la predicción abierta {forecast_id[:8]}?\n"
                "Se descarta sin puntuar: no cuenta ni a favor ni en contra."
            ),
            on_confirm,
        )

    def _handle_delete_resolved(self) -> None:
        forecast_id = self._selected_row_key(self.query_one("#resolved-table", DataTable))
        if forecast_id is None:
            self.notify("No hay predicciones resueltas.", severity="warning")
            return

        def on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return
            try:
                self.ledger.delete_resolved(forecast_id)
            except KeyError as e:
                self.notify(str(e), severity="error")
                return
            self.notify("La fiabilidad de su fuente se recalcula sin ella.",
                        title=f"Eliminada {forecast_id[:8]}")
            self._refresh_ledger()

        self.push_screen(
            ConfirmScreen(
                f"¿Eliminar la predicción resuelta {forecast_id[:8]}?\n"
                "OJO: borra historial de calibración — el Brier de su fuente\n"
                "se recalculará sin este dato."
            ),
            on_confirm,
        )

    # ── Workers (LLM en hilos: la UI no se bloquea) ───────────────────────

    @work(thread=True, exclusive=True, group="llm")
    def _fetch_models(self, provider: OllamaProvider, backend: str) -> None:
        try:
            models = provider.list_models()
        except OllamaUnavailableError as e:
            self._wlog(f"[red]No se pudieron listar modelos:[/red] {e}")
            self.call_from_thread(self.notify, str(e), title="Modelos", severity="error")
            return
        if not models:
            self._wlog(f"[yellow]El backend {backend} no tiene modelos disponibles.[/yellow]")
            return

        def apply() -> None:
            select = self.query_one("#model-select", Select)
            current = select.value
            select.set_options([(m, m) for m in models])
            select.value = current if current in models else models[0]
            self.notify(f"{len(models)} modelos disponibles en {backend}", title="Modelos")

        self.call_from_thread(apply)
        self._wlog(f"Modelos en {backend}: " + ", ".join(models[:10]) + ("…" if len(models) > 10 else ""))

    @work(thread=True, exclusive=True, group="llm")
    def _run_forecast(self, spec: CaseSpec, provider: OllamaProvider, model: str, backend: str) -> None:
        self._do_forecast(spec.question, spec.horizon, provider, model, backend, "#log")

    @work(thread=True, exclusive=True, group="llm")
    def _run_ask(
        self,
        question: str,
        horizon_days: int,
        full: bool,
        provider: OllamaProvider,
        model: str,
        backend: str,
    ) -> None:
        """El camino corto: solo una pregunta. Todo lo demás lo pone Oraqlo."""
        target = "#ask-log"
        log = lambda msg: self._wlog(msg, target)
        if not provider.is_available():
            log(f"[red]Backend no disponible[/red] ({provider.config.host}).")
            self.call_from_thread(self.notify, "Backend no disponible", severity="error")
            return

        # Fase Observe: investigación web (opcional; degrada con aviso, nunca bloquea).
        brief: ResearchBrief | None = None
        state: WorldState | None = None
        if self._research_enabled():
            brief = self._research(question, horizon_days, provider, model, target)
            if brief is not None:
                world = WorldModel()
                for obs in brief.as_observations():
                    world.apply(obs)
                state = world.current()

        if not full:
            self._do_forecast(
                question, timedelta(days=horizon_days), provider, model, backend, target,
                state=state, brief=brief,
            )
            return

        log(f"[bold]── Análisis completo ──[/bold] {model} @ {backend}")
        log("0/4 Redactando el caso (acciones candidatas, escenarios, utilidades)…")
        phase = time.monotonic()
        try:
            spec = draft_case(
                provider, model, question, horizon_days,
                context=brief.as_context() if brief else None,
            )
        except (DraftError, OllamaUnavailableError) as e:
            log(f"[red]Fallo redactando el caso:[/red] {e}")
            self.call_from_thread(self.notify, str(e), title="Análisis fallido", severity="error")
            return
        for action in spec.actions:
            outcomes = " · ".join(
                f"{label} {p:.0%}" for label, p in spec.outcomes[action.id].items()
            )
            log(f"  [b]{action.id}[/b]: {action.description}")
            log(f"    [dim]{outcomes}[/dim]")
        log(f"  [dim](caso redactado en {time.monotonic() - phase:.1f}s; "
            f"editable en la pestaña Caso)[/dim]")
        self.call_from_thread(self._load_drafted_case, spec)
        self._do_cycle(spec, provider, model, backend, target, state=state, brief=brief)

    @work(thread=True, exclusive=True, group="llm")
    def _run_goal(
        self, goal: str, horizon_days: int, provider: OllamaProvider, model: str, backend: str
    ) -> None:
        """Modo objetivo: qué tiene que pasar, qué puedes hacer, qué tienes que hacer."""
        target = "#ask-log"
        log = lambda msg: self._wlog(msg, target)
        if not provider.is_available():
            log(f"[red]Backend no disponible[/red] ({provider.config.host}).")
            self.call_from_thread(self.notify, "Backend no disponible", severity="error")
            return

        brief = None
        state = None
        if self._research_enabled():
            brief = self._research(goal, horizon_days, provider, model, target)
            if brief is not None:
                world = WorldModel()
                for obs in brief.as_observations():
                    world.apply(obs)
                state = world.current()

        log(f"[bold]── Lograr objetivo ──[/bold] {model} @ {backend}")
        started = time.monotonic()
        panel = SequentialPanel(roles={
            name: RoleConfig(name=name, provider=provider, model=model,
                             temperature=ROLE_TEMPERATURES[name])
            for name in ROLE_PROMPTS
        })
        strategist = GoalStrategist(provider, model)
        try:
            plan = strategist.pursue(
                goal, timedelta(days=horizon_days), panel, self.ledger,
                state=state, brief=brief,
                on_progress=lambda msg: log(f"[cyan]{msg}[/cyan]"),
            )
        except (GoalError, OllamaUnavailableError, PanelError, ForecastParseError) as e:
            log(f"[red]Fallo:[/red] {e}")
            self.call_from_thread(self.notify, str(e), title="Objetivo fallido", severity="error")
            return

        log("\n[bold]Qué puedes hacer (palancas):[/bold]")
        for lever in plan.levers:
            log(f"  · {lever.text}  [dim](impacto {lever.impact}, esfuerzo {lever.effort})[/dim]")
        if plan.external_conditions:
            log("[bold]Qué tiene que pasar (no lo controlas):[/bold]")
            for cond in plan.external_conditions:
                log(f"  · {cond.text}  [dim](P≈{cond.probability:.0%})[/dim]")

        log(f"\n[bold]El plan{' (re-diseñado tras el debate)' if plan.revised else ''}:[/bold] {plan.summary}")
        for step in plan.steps:
            log(f"  {step.order}. {step.action} — [dim]{step.when}[/dim]")
            if step.why:
                log(f"     [dim]{step.why}[/dim]")
        if plan.milestones:
            log("[bold]Hitos para saber si vas bien:[/bold]")
            for m in plan.milestones:
                log(f"  · {m.text} — {m.target_date}  [dim]({m.signal})[/dim]")

        if plan.verdict is not None:
            v = plan.verdict
            color = {"emitir": "green", "revisar": "yellow", "descartar": "red"}[v.decision]
            log(f"\nVeredicto del panel: [{color} bold]{v.decision.upper()}[/] "
                f"(confianza {v.confidence:.0%}) — {v.rationale}")
            for o in v.objections[:3]:
                log(f"  [yellow]objeción:[/yellow] {o}")

        sign = "+" if plan.uplift >= 0 else ""
        uplift_color = "green" if plan.uplift > 0 else "red"
        log(f"\n[bold {uplift_color}]P(objetivo): {plan.baseline_p:.0%} → "
            f"{plan.with_plan_p:.0%} con el plan ({sign}{plan.uplift:.0%})[/]")
        if plan.uplift <= 0:
            log("[red]El plan NO aumenta la probabilidad estimada: no inviertas esfuerzo "
                "en él sin revisarlo.[/red]")
        log(f"[dim]Ambas predicciones abiertas en el ledger; total {time.monotonic() - started:.1f}s. "
            f"'Exportar informe' disponible.[/dim]")

        tracked = self.call_from_thread(
            GoalTracker(self.goal_store, self.ledger).track, plan
        )
        log(f"[green]Seguimiento activado[/green] en la pestaña Objetivos "
            f"(hitos, check-ins y re-planificación). Id: {tracked.id[:8]}")
        self._last_run = {"question": goal, "goal_plan": plan, "forecast": plan.baseline,
                          "ranking": [], "verdict": plan.verdict, "model": model,
                          "backend": backend, "brief": brief, "tracked": tracked}
        self.call_from_thread(self._refresh_goals)
        self.call_from_thread(self._refresh_ledger)
        self.call_from_thread(
            self.notify,
            f"Uplift {sign}{plan.uplift:.0%} (de {plan.baseline_p:.0%} a {plan.with_plan_p:.0%})",
            title="Plan de objetivo listo",
        )

    def _research_enabled(self) -> bool:
        try:
            return self.query_one("#ask-research", Switch).value
        except Exception:
            return False

    def _research(
        self, question: str, horizon_days: int, provider: OllamaProvider, model: str, target: str
    ) -> ResearchBrief | None:
        """Fase de investigación web. Devuelve None (con aviso) si no se pudo investigar."""
        log = lambda msg: self._wlog(msg, target)
        web = OllamaWebClient()
        if not web.is_available():
            log("[yellow]Sin OLLAMA_API_KEY: continúo sin investigación web.[/yellow]")
            return None
        log("[bold]── Investigando en internet ──[/bold]")
        phase = time.monotonic()
        try:
            researcher = Researcher(web, provider, model)
            brief = researcher.investigate(
                question, timedelta(days=horizon_days),
                on_progress=lambda msg: log(f"  [cyan]{msg}[/cyan]"),
            )
        except (ResearchError, OllamaUnavailableError) as e:
            log(f"[yellow]Investigación no disponible ({e}); continúo sin ella.[/yellow]")
            return None
        for fact in brief.facts:
            log(f"  [green]hecho:[/green] {fact.text}")
            log(f"    [dim]{fact.source_url}[/dim]")
        log(f"  [dim]({len(brief.sources)} fuentes, {brief.rounds} ronda(s)"
            + (f", {len(brief.fetched_urls)} lectura(s) a fondo" if brief.fetched_urls else "")
            + f", en {time.monotonic() - phase:.1f}s)[/dim]")
        return brief

    def _load_drafted_case(self, spec: CaseSpec) -> None:
        """Carga el caso redactado por el LLM en la pestaña Caso para poder afinarlo."""
        data = case_to_dict(spec)
        self.query_one("#case-editor", TextArea).text = json.dumps(
            data, ensure_ascii=False, indent=2
        )
        if not self._json_mode:
            self.query_one("#case-form", CaseForm).load_dict(data)
        self._status("Caso redactado por el LLM desde la pestaña Preguntar.")

    def _do_forecast(
        self,
        question: str,
        horizon: timedelta,
        provider: OllamaProvider,
        model: str,
        backend: str,
        target: str,
        state: WorldState | None = None,
        brief: ResearchBrief | None = None,
    ) -> None:
        log = lambda msg: self._wlog(msg, target)
        log(f"[bold]── Previsión ──[/bold] {model} @ {backend}")
        if not provider.is_available():
            log(f"[red]Backend no disponible[/red] ({provider.config.host}).")
            self.call_from_thread(self.notify, "Backend no disponible", severity="error")
            return
        started = time.monotonic()
        try:
            forecaster = LLMForecaster(provider, model=model)
            forecast = forecaster.forecast(state or WorldModel().current(), question, horizon)
        except (OllamaUnavailableError, ForecastParseError) as e:
            log(f"[red]Fallo:[/red] {e}")
            self.call_from_thread(self.notify, str(e), title="Previsión fallida", severity="error")
            return
        self.call_from_thread(self.ledger.record, forecast)
        for label, p in sorted(forecast.distribution.outcomes.items(), key=lambda kv: -kv[1]):
            log(f"  {p:>6.1%}  {label}")
        for a in forecast.assumptions:
            log(f"  [dim]supuesto: {a}[/dim]")
        elapsed = time.monotonic() - started
        log(f"Previsión registrada ({elapsed:.1f}s) — id {forecast.id[:8]}, pestaña Ledger.")
        self._last_run = {"question": question, "forecast": forecast, "ranking": [],
                          "verdict": None, "model": model, "backend": backend, "brief": brief}
        self.call_from_thread(self._refresh_ledger)
        self.call_from_thread(self.notify, f"Previsión lista en {elapsed:.0f}s", title="Oraqlo")

    @work(thread=True, exclusive=True, group="llm")
    def _run_cycle(self, spec: CaseSpec, provider: OllamaProvider, model: str, backend: str) -> None:
        self._do_cycle(spec, provider, model, backend, "#log")

    def _do_cycle(
        self,
        spec: CaseSpec,
        provider: OllamaProvider,
        model: str,
        backend: str,
        target: str,
        state: WorldState | None = None,
        brief: ResearchBrief | None = None,
    ) -> None:
        log = lambda msg: self._wlog(msg, target)
        log(f"[bold]── Ciclo completo ──[/bold] {model} @ {backend}")
        if not provider.is_available():
            log(f"[red]Backend no disponible[/red] ({provider.config.host}).")
            self.call_from_thread(self.notify, "Backend no disponible", severity="error")
            return
        try:
            phase = time.monotonic()
            log("1/4 Previsión del oráculo…")
            forecaster = LLMForecaster(provider, model=model)
            state = state or WorldModel().current()
            forecast = forecaster.forecast(state, spec.question, spec.horizon)
            self.call_from_thread(self.ledger.record, forecast)
            for label, p in sorted(forecast.distribution.outcomes.items(), key=lambda kv: -kv[1]):
                log(f"  {p:>6.1%}  {label}")
            log(f"  [dim]({time.monotonic() - phase:.1f}s)[/dim]")

            log("2/4 Simulación y planificación…")
            engine = TreeSearchEngine(spec.outcome_model(), spec.utility_fn())
            trajectories = engine.expand(state, spec.actions, spec.depth)
            ranking = ExpectedUtilityPlanner().decide(trajectories, spec.risk)
            if not ranking:
                log("[red]El planificador descartó todas las acciones por riesgo de ruina.[/red]")
                return
            for rec in ranking:
                log(f"  {rec.action.id}: EU {rec.expected_utility:+.3f} · "
                    f"ajustada {rec.risk_adjusted_utility:+.3f}")
            for action, reason in ranking[0].rejected_alternatives:
                log(f"  [dim]descartada {action.id}: {reason}[/dim]")

            log(f"3/4 Panel deliberando sobre '{ranking[0].action.id}' — debate en vivo:")
            phase = time.monotonic()
            panel = SequentialPanel(roles={
                name: RoleConfig(name=name, provider=provider, model=model,
                                 temperature=ROLE_TEMPERATURES[name])
                for name in ROLE_PROMPTS
            })
            from oraqlo.agent import OraqloAgent

            context = OraqloAgent._build_context(spec.question, spec.horizon, forecast)
            if brief is not None:
                context += "\n\n" + brief.as_context()
            verdict = panel.deliberate(
                context, ranking[0], on_turn=lambda role, text: self._log_turn(role, text, target)
            )
            log(f"  [dim](debate: {time.monotonic() - phase:.1f}s)[/dim]")

            log("4/4 Veredicto:")
            color = {"emitir": "green", "revisar": "yellow", "descartar": "red"}[verdict.decision]
            log(f"  [{color} bold]{verdict.decision.upper()}[/] "
                f"(confianza {verdict.confidence:.0%}"
                + (f", P(éxito) {verdict.p_success:.0%}" if verdict.p_success is not None else "")
                + ")")
            log(f"  {verdict.rationale}")
            for t in verdict.review_triggers:
                log(f"  [dim]disparador de revisión: {t}[/dim]")
            log(f"Predicción {forecast.id[:8]} abierta en el ledger; 'Exportar informe' disponible.")
            self._last_run = {"question": spec.question, "forecast": forecast, "ranking": ranking,
                              "verdict": verdict, "model": model, "backend": backend,
                              "brief": brief}
            self.call_from_thread(self._refresh_ledger)
            self.call_from_thread(
                self.notify, f"Veredicto: {verdict.decision.upper()}", title="Ciclo completo"
            )
        except (OllamaUnavailableError, ForecastParseError, PanelError) as e:
            log(f"[red]Fallo:[/red] {e}")
            self.call_from_thread(self.notify, str(e), title="Ciclo fallido", severity="error")

    def _log_turn(self, role: str, text: str, target: str = "#log") -> None:
        """Callback del panel: muestra cada rol al completar su turno (hilo del worker)."""
        icons = {"estratega": "cyan", "red_team": "red", "oraculo": "magenta", "juez": "yellow"}
        color = icons.get(role, "white")
        summary = text.strip()
        if role == "red_team":
            try:
                objections = json.loads(summary).get("objections", [])
                summary = "\n".join(f"    · {o}" for o in objections)
            except json.JSONDecodeError:
                pass
        elif len(summary) > 600:
            summary = summary[:600] + " […]"
        self._wlog(f"  [{color} bold]{role}[/]:", target)
        self._wlog(f"  {summary}", target)


def main() -> None:
    import sys

    case = sys.argv[1] if len(sys.argv) > 1 else "cases/mi_caso.json"
    OraqloTUI(case_path=case).run()


if __name__ == "__main__":
    main()
