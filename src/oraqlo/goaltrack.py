"""Seguimiento de objetivos: check-ins, hitos y re-planificación.

El ciclo de vida completo: track (al crear el plan) → check_in (nota de avance →
P re-estimada; la trayectoria de P es la señal de progreso) → replan (cuando un
hito falla o la P cae) → close (logrado/no logrado → resuelve las predicciones
originales del ledger: Oraqlo aprende si sus planes funcionan).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from oraqlo.goals import (
    OUTCOME_ACHIEVED,
    OUTCOME_NOT_ACHIEVED,
    GoalPlan,
    Milestone,
    PlanStep,
    _PLAN_PROMPT,
)
from oraqlo.llm.base import LLMProvider
from oraqlo.llm.jsonchat import JsonChatError, chat_json
from oraqlo.memory.calibration import CalibrationLedger

MILESTONE_STATUSES = ("pendiente", "cumplido", "fallado")
GOAL_STATUSES = ("activo", "logrado", "no-logrado", "abandonado")

_REEVAL_PROMPT = (
    "Eres el Oráculo de Oraqlo: probabilidades calibradas, nunca certezas. "
    "Re-estima la probabilidad de que el objetivo se logre antes de su fecha "
    "límite, dado el plan, el estado de los hitos, la trayectoria previa de P y "
    "la nota de avance del usuario. Sé sensible a la evidencia: hitos cumplidos "
    "suben P, hitos fallados/vencidos y notas de estancamiento la bajan. "
    'Responde SOLO con JSON: {"p_logro": <prob en [0,1]>, '
    '"comment": "<2 frases: cómo va y qué es lo más importante ahora>", '
    '"replan_recommended": <true|false>} — en el idioma del objetivo.'
)


class GoalTrackError(RuntimeError):
    """La operación de seguimiento no pudo completarse."""


@dataclass
class TrackedMilestone:
    text: str
    target_date: str  # AAAA-MM-DD
    signal: str
    status: str = "pendiente"

    @property
    def overdue(self) -> bool:
        if self.status != "pendiente":
            return False
        try:
            return date.fromisoformat(self.target_date) < date.today()
        except ValueError:
            return False


@dataclass
class CheckIn:
    date: str  # ISO
    note: str
    p_estimate: float
    comment: str = ""
    replan_recommended: bool = False


@dataclass
class TrackedGoal:
    id: str
    goal: str
    created_at: str
    deadline: str  # AAAA-MM-DD
    summary: str
    steps: list[PlanStep]
    milestones: list[TrackedMilestone]
    checkins: list[CheckIn] = field(default_factory=list)
    baseline_forecast_id: str = ""
    plan_forecast_id: str = ""
    baseline_p: float = 0.0
    initial_p: float = 0.0
    status: str = "activo"
    plan_version: int = 1

    @property
    def current_p(self) -> float:
        return self.checkins[-1].p_estimate if self.checkins else self.initial_p

    @property
    def trajectory(self) -> list[float]:
        return [self.initial_p, *(c.p_estimate for c in self.checkins)]

    @property
    def days_left(self) -> int:
        try:
            return (date.fromisoformat(self.deadline) - date.today()).days
        except ValueError:
            return 0

    @property
    def next_milestone(self) -> TrackedMilestone | None:
        pending = [m for m in self.milestones if m.status == "pendiente"]
        return min(pending, key=lambda m: m.target_date) if pending else None


def _goal_to_dict(g: TrackedGoal) -> dict:
    return asdict(g)


def _goal_from_dict(data: dict) -> TrackedGoal:
    return TrackedGoal(
        id=data["id"], goal=data["goal"], created_at=data["created_at"],
        deadline=data["deadline"], summary=data.get("summary", ""),
        steps=[PlanStep(**s) for s in data.get("steps", [])],
        milestones=[TrackedMilestone(**m) for m in data.get("milestones", [])],
        checkins=[CheckIn(**c) for c in data.get("checkins", [])],
        baseline_forecast_id=data.get("baseline_forecast_id", ""),
        plan_forecast_id=data.get("plan_forecast_id", ""),
        baseline_p=float(data.get("baseline_p", 0.0)),
        initial_p=float(data.get("initial_p", 0.0)),
        status=data.get("status", "activo"),
        plan_version=int(data.get("plan_version", 1)),
    )


class GoalStore:
    """Persistencia JSON de objetivos trackeados (patrón de memory/store.py)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._goals: dict[str, TrackedGoal] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for item in data.get("goals", []):
            goal = _goal_from_dict(item)
            self._goals[goal.id] = goal

    def _save(self) -> None:
        payload = {"goals": [_goal_to_dict(g) for g in self._goals.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, goal: TrackedGoal) -> None:
        self._goals[goal.id] = goal
        self._save()

    def get(self, goal_id: str) -> TrackedGoal:
        if goal_id not in self._goals:
            raise KeyError(f"No hay objetivo con id '{goal_id}'.")
        return self._goals[goal_id]

    def update(self, goal: TrackedGoal) -> None:
        self._goals[goal.id] = goal
        self._save()

    def delete(self, goal_id: str) -> None:
        if goal_id not in self._goals:
            raise KeyError(f"No hay objetivo con id '{goal_id}'.")
        del self._goals[goal_id]
        self._save()

    def list_active(self) -> list[TrackedGoal]:
        return [g for g in self._goals.values() if g.status == "activo"]

    def list_all(self) -> list[TrackedGoal]:
        return list(self._goals.values())


class GoalTracker:
    def __init__(
        self,
        store: GoalStore,
        ledger: CalibrationLedger,
        provider: LLMProvider | None = None,
        model: str = "",
        max_retries: int = 1,
    ) -> None:
        self.store = store
        self.ledger = ledger
        self.provider = provider
        self.model = model
        self.max_retries = max_retries

    # ── Alta ──────────────────────────────────────────────────────────────

    def track(self, plan: GoalPlan) -> TrackedGoal:
        """Convierte un GoalPlan recién creado en objetivo con seguimiento."""
        deadline = (date.today() + plan.horizon).isoformat()
        goal = TrackedGoal(
            id=str(uuid.uuid4()),
            goal=plan.goal,
            created_at=datetime.now(timezone.utc).isoformat(),
            deadline=deadline,
            summary=plan.summary,
            steps=list(plan.steps),
            milestones=[
                TrackedMilestone(text=m.text, target_date=m.target_date, signal=m.signal)
                for m in plan.milestones
            ],
            baseline_forecast_id=plan.baseline.id,
            plan_forecast_id=plan.with_plan.id,
            baseline_p=plan.baseline_p,
            initial_p=plan.with_plan_p,
        )
        self.store.add(goal)
        return goal

    # ── Seguimiento ───────────────────────────────────────────────────────

    def _require_llm(self) -> None:
        if self.provider is None or not self.model:
            raise GoalTrackError("Esta operación necesita un proveedor LLM configurado.")

    @staticmethod
    def _status_block(g: TrackedGoal) -> str:
        milestone_lines = []
        for m in g.milestones:
            status = m.status + (" (VENCIDO sin marcar)" if m.overdue else "")
            milestone_lines.append(f"- {m.text} · fecha objetivo {m.target_date} · {status}")
        trajectory = " → ".join(f"{p:.0%}" for p in g.trajectory)
        checkin_lines = [f"- {c.date[:10]}: {c.note}" for c in g.checkins[-5:]]
        return (
            f"Fecha de hoy: {date.today().isoformat()}\n"
            f"Objetivo: {g.goal}\n"
            f"Fecha límite: {g.deadline} ({g.days_left} días restantes)\n"
            f"Plan (v{g.plan_version}): {g.summary}\n"
            f"Hitos:\n" + "\n".join(milestone_lines) + "\n"
            f"Trayectoria de P(objetivo): {trajectory}\n"
            + ("Últimos check-ins:\n" + "\n".join(checkin_lines) if checkin_lines else "")
        )

    def check_in(self, goal_id: str, note: str) -> CheckIn:
        """Registra avance y re-estima P(objetivo). La trayectoria es la señal."""
        self._require_llm()
        g = self.store.get(goal_id)
        if g.status != "activo":
            raise GoalTrackError(f"El objetivo está '{g.status}': no admite check-ins.")
        try:
            data = chat_json(
                self.provider, self.model, _REEVAL_PROMPT,
                self._status_block(g) + f"\n\nNota de avance del usuario (hoy): {note}",
                temperature=0.2, max_retries=self.max_retries,
            )
        except JsonChatError as e:
            raise GoalTrackError(str(e)) from e
        try:
            p = min(max(float(data["p_logro"]), 0.0), 1.0)
        except (KeyError, TypeError, ValueError) as e:
            raise GoalTrackError(f"Re-evaluación sin 'p_logro' numérico: {e}") from e
        replan = bool(data.get("replan_recommended", False)) or any(
            m.status == "fallado" or m.overdue for m in g.milestones
        )
        checkin = CheckIn(
            date=datetime.now(timezone.utc).isoformat(),
            note=note,
            p_estimate=p,
            comment=str(data.get("comment", "")),
            replan_recommended=replan,
        )
        g.checkins.append(checkin)
        self.store.update(g)
        return checkin

    def set_milestone(self, goal_id: str, index: int, status: str) -> TrackedMilestone:
        if status not in MILESTONE_STATUSES:
            raise GoalTrackError(f"Estado de hito inválido: {status!r}")
        g = self.store.get(goal_id)
        if not 0 <= index < len(g.milestones):
            raise GoalTrackError(f"No existe el hito {index} en '{g.goal[:40]}'.")
        g.milestones[index].status = status
        self.store.update(g)
        return g.milestones[index]

    def replan(self, goal_id: str) -> TrackedGoal:
        """Re-genera pasos e hitos pendientes con el estado actual; conserva los cumplidos."""
        self._require_llm()
        g = self.store.get(goal_id)
        if g.status != "activo":
            raise GoalTrackError(f"El objetivo está '{g.status}': no admite re-planificación.")
        try:
            data = chat_json(
                self.provider, self.model, _PLAN_PROMPT,
                self._status_block(g)
                + "\n\nRe-planifica: el plan anterior no va bien. Conserva lo ganado, "
                  "corrige lo fallado, y ajusta a los días restantes.",
                temperature=0.5, max_retries=self.max_retries,
            )
        except JsonChatError as e:
            raise GoalTrackError(str(e)) from e
        steps = [
            PlanStep(order=int(s.get("order", i + 1)), action=str(s.get("action", "")).strip(),
                     when=str(s.get("when", "")).strip(), why=str(s.get("why", "")).strip())
            for i, s in enumerate(data.get("steps", []))
            if isinstance(s, dict) and str(s.get("action", "")).strip()
        ]
        if not steps:
            raise GoalTrackError("La re-planificación no produjo pasos.")
        new_milestones = [
            TrackedMilestone(text=str(m.get("text", "")).strip(),
                             target_date=str(m.get("target_date", "")).strip(),
                             signal=str(m.get("signal", "")).strip())
            for m in data.get("milestones", [])
            if isinstance(m, dict) and str(m.get("text", "")).strip()
        ]
        kept = [m for m in g.milestones if m.status == "cumplido"]
        g.summary = str(data.get("summary", g.summary)).strip() or g.summary
        g.steps = steps
        g.milestones = kept + new_milestones
        g.plan_version += 1
        self.store.update(g)
        return g

    # ── Cierre ────────────────────────────────────────────────────────────

    def close(self, goal_id: str, achieved: bool) -> list[str]:
        """Cierra el objetivo y resuelve sus predicciones en el ledger.

        Devuelve avisos (p. ej. forecasts ya resueltos a mano) en vez de fallar.
        """
        g = self.store.get(goal_id)
        g.status = "logrado" if achieved else "no-logrado"
        self.store.update(g)
        outcome = OUTCOME_ACHIEVED if achieved else OUTCOME_NOT_ACHIEVED
        warnings = []
        for fid, label in ((g.baseline_forecast_id, "statu quo"),
                           (g.plan_forecast_id, "con plan")):
            if not fid:
                continue
            try:
                resolved = self.ledger.resolve(fid, outcome)
                warnings.append(f"predicción [{label}] resuelta: Brier {resolved.brier:.3f}")
            except KeyError:
                warnings.append(f"predicción [{label}] ya no estaba abierta (resuelta a mano).")
        return warnings

    def delete(self, goal_id: str) -> list[str]:
        """Elimina un objetivo por completo (distinto de cerrarlo).

        Sus predicciones ABIERTAS se descartan del ledger sin puntuar (no
        afectan a la calibración); las ya resueltas se conservan como historial.
        Devuelve avisos de lo que se tocó en el ledger.
        """
        g = self.store.get(goal_id)
        warnings = []
        for fid, label in ((g.baseline_forecast_id, "statu quo"),
                           (g.plan_forecast_id, "con plan")):
            if not fid:
                continue
            try:
                self.ledger.discard(fid)
                warnings.append(f"predicción [{label}] descartada del ledger (sin puntuar).")
            except KeyError:
                pass  # ya resuelta (se conserva) o inexistente: nada que hacer
        self.store.delete(goal_id)
        return warnings

    def overdue_milestones(self) -> list[tuple[TrackedGoal, TrackedMilestone]]:
        return [
            (g, m)
            for g in self.store.list_active()
            for m in g.milestones
            if m.overdue
        ]
