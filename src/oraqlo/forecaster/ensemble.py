"""Previsiones con miembros: la combinada y lo que dijo cada modelo por separado.

Lo que dijo cada miembro de un comité (ver forecaster.committee) se registra con id
`<id-de-la-previsión>::<modelo>`; `resolve_group()` las cierra todas a la vez, así
cada modelo acumula su propio historial y los pesos del comité aprenden con el uso.
"""

from __future__ import annotations

from oraqlo.memory.calibration import CalibrationLedger

MEMBER_SEP = "::"


def resolve_group(ledger: CalibrationLedger, forecast_id: str, actual_outcome: str) -> int:
    """Resuelve una predicción y las de sus miembros (`<id>::<modelo>`). Devuelve cuántas cerró."""
    ids = [forecast_id] + [
        f.id for f in ledger.open_forecasts if f.id.startswith(forecast_id + MEMBER_SEP)
    ]
    for fid in ids:
        ledger.resolve(fid, actual_outcome)
    return len(ids)
