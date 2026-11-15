"""SqliteGoalStore: objetivos con seguimiento, persistidos como documentos JSON."""

from __future__ import annotations

from oraqlo.goaltrack import GoalStore, TrackedGoal, _goal_from_dict, _goal_to_dict
from oraqlo.store.db import Database, dumps, loads


class SqliteGoalStore(GoalStore):
    def __init__(self, db: Database) -> None:  # no llama a GoalStore.__init__ (es de archivo)
        self.db = db
        self._goals: dict[str, TrackedGoal] = {}
        self._load()

    def _load(self) -> None:
        with self.db.lock:
            self._goals = {}
            for row in self.db.query("SELECT data FROM goals ORDER BY rowid"):
                goal = _goal_from_dict(loads(row["data"]))
                self._goals[goal.id] = goal

    def _save(self) -> None:  # escritura fila a fila en add/update/delete
        pass

    def _upsert(self, goal: TrackedGoal) -> None:
        with self.db.tx() as conn:
            self._goals[goal.id] = goal
            conn.execute(
                """INSERT INTO goals (id, status, data, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(id) DO UPDATE SET status = excluded.status,
                       data = excluded.data, updated_at = excluded.updated_at""",
                (goal.id, goal.status, dumps(_goal_to_dict(goal))),
            )

    def add(self, goal: TrackedGoal) -> None:
        self._upsert(goal)

    def update(self, goal: TrackedGoal) -> None:
        self._upsert(goal)

    def delete(self, goal_id: str) -> None:
        with self.db.tx() as conn:
            if goal_id not in self._goals:
                raise KeyError(f"No hay objetivo con id '{goal_id}'.")
            del self._goals[goal_id]
            conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
