"""StaticConnector: entrega una lista fija de observaciones, una sola vez.

Útil para demos, tests y para inyectar hechos conocidos al arrancar un caso.
"""

from __future__ import annotations

from collections.abc import Iterator

from oraqlo.connectors.base import Connector
from oraqlo.world_model import Observation


class StaticConnector(Connector):
    def __init__(self, source_id: str, observations: list[Observation]) -> None:
        super().__init__(source_id)
        self._pending = list(observations)

    def poll(self) -> Iterator[Observation]:
        pending, self._pending = self._pending, []
        yield from pending
