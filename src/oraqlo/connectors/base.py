"""Conectores: normalizan datos heterogéneos hacia Observations del World Model."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from oraqlo.world_model import Observation


class Connector(ABC):
    """Fuente de observaciones enchufable.

    Contrato:
    - `poll()` devuelve las observaciones nuevas desde la última llamada (puede
      ser vacío); nunca repite observaciones ya entregadas.
    - Toda observación lleva `source` (id del conector) y timestamp del evento
      original, no del momento de ingesta.

    Implementaciones previstas: FileConnector (CSV/JSON), APIConnector (HTTP
    polling), ManualConnector (entrada humana interactiva).
    """

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    @abstractmethod
    def poll(self) -> Iterator[Observation]:
        """Observaciones nuevas desde el último poll."""
        ...
