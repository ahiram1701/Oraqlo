"""FileConnector: observaciones desde un archivo CSV, JSON o JSONL.

Columnas/campos: `entity_id`, `variable`, `value` y opcional `timestamp` (ISO).
Cada `poll()` relee el archivo y entrega solo las filas nuevas (las ya
entregadas se recuerdan por contenido), así que sirve para archivos a los que
otro proceso va añadiendo datos.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oraqlo.connectors.base import Connector
from oraqlo.world_model import Observation


def _coerce(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return float(value) if any(c in value for c in ".eE") else int(value)
        except ValueError:
            return value
    return value


class FileConnector(Connector):
    def __init__(self, path: str | Path, source_id: str | None = None) -> None:
        self.path = Path(path)
        super().__init__(source_id or f"file:{self.path.name}")
        self._seen: set[tuple] = set()

    def _rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        text = self.path.read_text(encoding="utf-8")
        suffix = self.path.suffix.lower()
        if suffix == ".csv":
            return list(csv.DictReader(text.splitlines()))
        if suffix == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        data = json.loads(text)
        return data if isinstance(data, list) else data.get("observations", [])

    def poll(self) -> Iterator[Observation]:
        for row in self._rows():
            key = (
                row.get("entity_id"),
                row.get("variable"),
                str(row.get("value")),
                row.get("timestamp"),
            )
            if key in self._seen:
                continue
            self._seen.add(key)
            ts = row.get("timestamp")
            timestamp = datetime.fromisoformat(ts) if ts else datetime.now(UTC)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            yield Observation(
                entity_id=str(row["entity_id"]),
                variable=str(row["variable"]),
                value=_coerce(row["value"]),
                source=self.source_id,
                timestamp=timestamp,
            )
