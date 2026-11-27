"""SqliteLedger: el CalibrationLedger persistido fila a fila en SQLite.

Hereda toda la lógica (Brier, fiabilidad, descartes) y solo cambia dónde vive:
cada operación escribe su fila en una transacción, con el lock tomado para que
memoria y disco no diverjan aunque escriban varios hilos (jobs, scheduler).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import datetime, timedelta

from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.memory.calibration import CalibrationLedger, ResolvedForecast
from oraqlo.store.db import Database, dumps, loads


def _row_to_forecast(row) -> Forecast:
    return Forecast(
        id=row["id"],
        question=row["question"],
        distribution=Distribution(
            outcomes={str(k): float(v) for k, v in loads(row["outcomes"]).items()}
        ),
        horizon=timedelta(days=row["horizon_days"]),
        assumptions=list(loads(row["assumptions"]) or []),
        source=row["source"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
    )


def _forecast_params(f: Forecast) -> dict:
    return {
        "id": f.id,
        "question": f.question,
        "source": f.source,
        "outcomes": dumps(f.distribution.outcomes),
        "horizon_days": f.horizon.total_seconds() / 86400,
        "assumptions": dumps(f.assumptions),
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


class SqliteLedger(CalibrationLedger):
    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self.reload()

    def reload(self) -> None:
        """Relee el estado desde la base (por si otro proceso la modificó)."""
        with self.db.lock:
            self._open = {}
            self._resolved = []
            for row in self.db.query(
                "SELECT * FROM forecasts WHERE status = 'open' ORDER BY rowid"
            ):
                self._open[row["id"]] = _row_to_forecast(row)
            for row in self.db.query(
                "SELECT * FROM forecasts WHERE status = 'resolved' ORDER BY seq, rowid"
            ):
                self._resolved.append(
                    ResolvedForecast(
                        forecast=_row_to_forecast(row),
                        actual_outcome=row["actual_outcome"],
                        brier=float(row["brier"]),
                        resolved_at=datetime.fromisoformat(row["resolved_at"]),
                    )
                )

    @contextlib.contextmanager
    def _tx(self) -> Iterator:
        """Transacción en la base y, si falla, la memoria vuelve a como estaba: sin esto,
        un error al escribir dejaba la memoria cambiada y el disco no."""
        with self.db.lock:  # reentrante: tx() lo vuelve a tomar
            open_before, resolved_before = dict(self._open), list(self._resolved)
            try:
                with self.db.tx() as conn:
                    yield conn
            except BaseException:  # incluido un COMMIT que falla
                self._open, self._resolved = open_before, resolved_before
                raise

    def record(self, forecast: Forecast) -> None:
        with self._tx() as conn:
            super().record(forecast)
            conn.execute(
                """INSERT INTO forecasts (id, question, source, outcomes, horizon_days,
                       assumptions, created_at, status)
                   VALUES (:id, :question, :source, :outcomes, :horizon_days,
                       :assumptions, :created_at, 'open')
                   ON CONFLICT(id) DO UPDATE SET question = excluded.question,
                       source = excluded.source, outcomes = excluded.outcomes,
                       horizon_days = excluded.horizon_days, assumptions = excluded.assumptions,
                       status = 'open', actual_outcome = NULL, brier = NULL, resolved_at = NULL""",
                _forecast_params(forecast),
            )

    def resolve(self, forecast_id: str, actual_outcome: str) -> ResolvedForecast:
        with self._tx() as conn:
            resolved = super().resolve(forecast_id, actual_outcome)
            conn.execute(
                """UPDATE forecasts SET status = 'resolved', actual_outcome = ?, brier = ?,
                       resolved_at = ?, seq = (SELECT COALESCE(MAX(seq), 0) + 1 FROM forecasts)
                   WHERE id = ?""",
                (
                    resolved.actual_outcome,
                    resolved.brier,
                    resolved.resolved_at.isoformat(),
                    forecast_id,
                ),
            )
        return resolved

    def discard(self, forecast_id: str) -> Forecast:
        with self._tx() as conn:
            forecast = super().discard(forecast_id)
            conn.execute("DELETE FROM forecasts WHERE id = ? AND status = 'open'", (forecast_id,))
        return forecast

    def delete_resolved(self, forecast_id: str) -> ResolvedForecast:
        with self._tx() as conn:
            resolved = super().delete_resolved(forecast_id)
            conn.execute(
                "DELETE FROM forecasts WHERE id = ? AND status = 'resolved'", (forecast_id,)
            )
        return resolved

    def import_resolved(self, resolved: ResolvedForecast) -> None:
        """Inserta una predicción ya resuelta tal cual (migración de datos legados)."""
        with self.db.tx() as conn:
            if any(r.forecast.id == resolved.forecast.id for r in self._resolved):
                return
            self._resolved.append(resolved)
            params = _forecast_params(resolved.forecast)
            params.update(
                actual_outcome=resolved.actual_outcome,
                brier=resolved.brier,
                resolved_at=resolved.resolved_at.isoformat(),
            )
            conn.execute(
                """INSERT OR IGNORE INTO forecasts (id, question, source, outcomes, horizon_days,
                       assumptions, created_at, status, actual_outcome, brier, resolved_at, seq)
                   VALUES (:id, :question, :source, :outcomes, :horizon_days, :assumptions,
                       :created_at, 'resolved', :actual_outcome, :brier, :resolved_at,
                       (SELECT COALESCE(MAX(seq), 0) + 1 FROM forecasts))""",
                params,
            )
