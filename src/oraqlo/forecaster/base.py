"""Forecaster (el oráculo): distribuciones sobre estados futuros, nunca certezas.

Invariante (ADR 0001): toda salida es una Distribution. Un forecaster que emita
valores puntuales sin incertidumbre viola el contrato del sistema.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from oraqlo.world_model import WorldState


@dataclass
class Distribution:
    """Distribución de probabilidad sobre resultados posibles.

    Para variables categóricas/escenarios: `outcomes` mapea etiqueta → probabilidad
    (deben sumar ~1.0). Para numéricas: `interval` es el intervalo de predicción
    (low, high) al nivel `interval_level`, con `point` como central opcional.
    """

    outcomes: dict[str, float] = field(default_factory=dict)
    point: float | None = None
    interval: tuple[float, float] | None = None
    interval_level: float = 0.9

    def validate(self) -> None:
        """Falla si la distribución no es coherente (probabilidades fuera de [0,1] o no suman 1)."""
        if self.outcomes:
            total = sum(self.outcomes.values())
            if not 0.99 <= total <= 1.01:
                raise ValueError(f"Las probabilidades suman {total:.3f}, no 1.0")
            if any(not 0.0 <= p <= 1.0 for p in self.outcomes.values()):
                raise ValueError("Probabilidad fuera de [0, 1]")
        if self.interval is not None and self.interval[0] > self.interval[1]:
            raise ValueError("Intervalo invertido: low > high")


@dataclass
class Forecast:
    """Predicción registrable: qué se predijo, con qué confianza, y sus supuestos.

    `assumptions` es obligatorio en espíritu: un forecast sin supuestos explícitos
    no es auditable. `id` permite cerrar la predicción con el resultado real en
    memory.calibration cuando este llegue.
    """

    id: str
    question: str
    distribution: Distribution
    horizon: timedelta
    assumptions: list[str] = field(default_factory=list)
    source: str = "unknown"
    created_at: datetime | None = None


class Forecaster(ABC):
    """Contrato del oráculo.

    Implementaciones previstas:
    - QuantForecaster: series temporales / Bayes para variables numéricas.
    - LLMForecaster: razonamiento vía Ollama para escenarios y probabilidades
      subjetivas donde no hay datos.
    - EnsembleForecaster: combina las anteriores ponderando por su Brier
      histórico (memory.calibration).
    """

    @abstractmethod
    def forecast(self, state: WorldState, question: str, horizon: timedelta) -> Forecast:
        """Distribución sobre la respuesta a `question` en el horizonte dado.

        Debe degradar la confianza con el horizonte: a más lejos, más ancho el
        intervalo / más entropía en outcomes.
        """
        ...
