"""World Model: representación estructurada y agnóstica al dominio del estado.

Única fuente de verdad para el resto de módulos. Versionado en el tiempo para
permitir rebobinar y razonar contrafactualmente ("¿qué habría pasado si...?").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Epistemic(Enum):
    """Estatus epistémico de una variable: qué tan bien la conocemos.

    Invariante del oráculo honesto: nada se marca KNOWN sin observación directa.
    """

    KNOWN = "known"          # observado directamente, fuente confiable
    ESTIMATED = "estimated"  # inferido/predicho, lleva incertidumbre
    UNKNOWN = "unknown"      # reconocidamente ignorado; el planner lo trata como riesgo


@dataclass
class Variable:
    """Una magnitud del dominio con su incertidumbre explícita.

    `value` es el valor central; `uncertainty` su dispersión (interpretación
    según el tipo: desviación típica para numéricas, entropía para categóricas).
    """

    name: str
    value: Any
    epistemic: Epistemic = Epistemic.ESTIMATED
    uncertainty: float | None = None
    source: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Entity:
    """Actor u objeto del dominio (empresa, jugador, activo, recurso...)."""

    id: str
    kind: str
    variables: dict[str, Variable] = field(default_factory=dict)


@dataclass
class Relation:
    """Relación dirigida entre entidades (depende-de, compite-con, causa...)."""

    source_id: str
    target_id: str
    kind: str
    weight: float = 1.0


@dataclass
class Observation:
    """Observación normalizada que entra desde un Connector."""

    entity_id: str
    variable: str
    value: Any
    source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class WorldState:
    """Instantánea versionada del mundo en un momento dado."""

    version: int
    timestamp: datetime
    entities: dict[str, Entity] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)


class WorldModel:
    """Mantiene el historial de estados y aplica observaciones.

    Contrato:
    - `apply(obs)` crea una nueva versión (los estados son inmutables una vez creados).
    - `current()` devuelve la última versión; `at(t)` la vigente en el instante t.
    - Nunca elimina historial: la trazabilidad es un principio de diseño.
    """

    def __init__(self) -> None:
        self._history: list[WorldState] = [
            WorldState(version=0, timestamp=datetime.now(timezone.utc))
        ]

    def current(self) -> WorldState:
        return self._history[-1]

    def apply(self, obs: Observation) -> WorldState:
        """Integra una observación y devuelve el nuevo estado (nueva versión).

        Los estados previos no se mutan: se copian las entidades afectadas.
        Una variable observada directamente queda marcada KNOWN — es el único
        camino por el que algo llega a KNOWN (invariante del oráculo honesto).
        """
        prev = self.current()
        entities = {
            eid: Entity(id=e.id, kind=e.kind, variables=dict(e.variables))
            for eid, e in prev.entities.items()
        }
        entity = entities.get(obs.entity_id)
        if entity is None:
            entity = Entity(id=obs.entity_id, kind="unknown")
            entities[obs.entity_id] = entity
        entity.variables[obs.variable] = Variable(
            name=obs.variable,
            value=obs.value,
            epistemic=Epistemic.KNOWN,
            uncertainty=None,
            source=obs.source,
            updated_at=obs.timestamp,
        )
        new_state = WorldState(
            version=prev.version + 1,
            timestamp=obs.timestamp,
            entities=entities,
            relations=list(prev.relations),
        )
        self._history.append(new_state)
        return new_state

    def at(self, timestamp: datetime) -> WorldState:
        """Estado vigente en `timestamp` (para rebobinar / contrafactuales).

        Devuelve la última versión cuyo timestamp es <= `timestamp`; si la
        marca es anterior a todo el historial, devuelve la versión 0 (el
        estado vacío inicial es lo único que se sabía entonces).
        """
        for state in reversed(self._history):
            if state.timestamp <= timestamp:
                return state
        return self._history[0]
