"""Simulador de escenarios: expande futuros a partir de las distribuciones del oráculo.

Es lo que convierte a Oraqlo en estratega y no solo pronosticador: explora
secuencias de decisiones y asume oponentes que también optimizan.

El dominio entra por tres funciones enchufables:
- OutcomeModel(state, action) -> Distribution categórica de lo que puede pasar.
- TransitionFn(state, action, outcome_label) -> nuevo estado (opcional; por
  defecto el estado no cambia y la utilidad depende del camino de outcomes).
- UtilityFn(final_state, path) -> utilidad de la trayectoria completa.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from oraqlo.forecaster.base import Distribution
from oraqlo.world_model import WorldState


@dataclass
class Action:
    """Acción candidata que el agente (o su adversario) puede tomar."""

    id: str
    description: str
    params: dict = field(default_factory=dict)


@dataclass
class Trajectory:
    """Una trayectoria simulada: estados con su probabilidad y utilidad.

    Las probabilidades suman ~1.0 DENTRO de cada grupo de acción inicial
    (son distribuciones condicionadas a la acción; el planificador agrupa
    por `actions_taken[0]`).
    """

    states: list[WorldState]
    probability: float
    utility: float
    actions_taken: list[Action] = field(default_factory=list)


OutcomeModel = Callable[[WorldState, Action], Distribution]
TransitionFn = Callable[[WorldState, Action, str], WorldState]
UtilityFn = Callable[[WorldState, tuple[str, ...]], float]


class ScenarioEngine(ABC):
    """Contrato común de los motores de simulación."""

    @abstractmethod
    def expand(self, state: WorldState, actions: list[Action], depth: int) -> list[Trajectory]:
        """Genera trayectorias futuras desde `state` considerando `actions`.

        `depth` es el número de pasos de decisión a explorar.
        """
        ...


def _sample_outcome(dist: Distribution, rng: random.Random) -> str:
    if not dist.outcomes:
        raise ValueError("El OutcomeModel debe devolver una distribución categórica (outcomes)")
    labels = list(dist.outcomes)
    return rng.choices(labels, weights=[dist.outcomes[label] for label in labels], k=1)[0]


class MonteCarloEngine(ScenarioEngine):
    """Propaga incertidumbre muestreando rollouts y agrupándolos por camino.

    Política de rollout: la acción inicial es la evaluada; en pasos posteriores
    se elige uniformemente entre las candidatas (rollout estándar). Con `seed`
    fijo el resultado es reproducible.
    """

    def __init__(
        self,
        outcome_model: OutcomeModel,
        utility_fn: UtilityFn,
        transition: TransitionFn | None = None,
        n_samples: int = 1000,
        seed: int | None = None,
    ) -> None:
        self.outcome_model = outcome_model
        self.utility_fn = utility_fn
        self.transition = transition
        self.n_samples = n_samples
        self.seed = seed

    def expand(self, state: WorldState, actions: list[Action], depth: int) -> list[Trajectory]:
        if not actions:
            raise ValueError("Se necesita al menos una acción candidata")
        if depth < 1:
            raise ValueError("depth debe ser >= 1")
        rng = random.Random(self.seed)
        trajectories: list[Trajectory] = []

        for initial in actions:
            # camino de outcomes → [conteo, suma de utilidad, estado final ejemplo]
            buckets: dict[tuple[str, ...], list] = {}
            for _ in range(self.n_samples):
                s, act, path = state, initial, []
                for step in range(depth):
                    label = _sample_outcome(self.outcome_model(s, act), rng)
                    path.append(label)
                    if self.transition is not None:
                        s = self.transition(s, act, label)
                    if step + 1 < depth:
                        act = rng.choice(actions)
                key = tuple(path)
                utility = self.utility_fn(s, key)
                bucket = buckets.setdefault(key, [0, 0.0, s])
                bucket[0] += 1
                bucket[1] += utility

            for key, (count, usum, final_state) in buckets.items():
                trajectories.append(
                    Trajectory(
                        states=[state, final_state],
                        probability=count / self.n_samples,
                        utility=usum / count,
                        actions_taken=[initial],
                    )
                )
        return trajectories


class TreeSearchEngine(ScenarioEngine):
    """Expectimax exacto: enumera outcomes y asume continuación óptima.

    En cada nivel de decisión elige la mejor acción (max); en cada nivel de
    azar pondera por probabilidad (expectation). Coste O((|A|·|O|)^depth):
    para árboles grandes, usar MonteCarloEngine o un MCTS futuro.
    Devuelve, por acción raíz, una trayectoria por primer outcome con la
    utilidad de la continuación óptima — así el planner ve la distribución
    real de cada acción y puede aplicar CVaR.
    """

    def __init__(
        self,
        outcome_model: OutcomeModel,
        utility_fn: UtilityFn,
        transition: TransitionFn | None = None,
    ) -> None:
        self.outcome_model = outcome_model
        self.utility_fn = utility_fn
        self.transition = transition

    def _next(self, state: WorldState, action: Action, label: str) -> WorldState:
        return self.transition(state, action, label) if self.transition else state

    def _value(self, state: WorldState, path: list[str], actions: list[Action], depth: int) -> float:
        if depth == 0:
            return self.utility_fn(state, tuple(path))
        best = -math.inf
        for action in actions:
            dist = self.outcome_model(state, action)
            if not dist.outcomes:
                raise ValueError("El OutcomeModel debe devolver outcomes categóricos")
            ev = sum(
                p * self._value(self._next(state, action, label), [*path, label], actions, depth - 1)
                for label, p in dist.outcomes.items()
            )
            best = max(best, ev)
        return best

    def expand(self, state: WorldState, actions: list[Action], depth: int) -> list[Trajectory]:
        if not actions:
            raise ValueError("Se necesita al menos una acción candidata")
        if depth < 1:
            raise ValueError("depth debe ser >= 1")
        trajectories: list[Trajectory] = []
        for action in actions:
            dist = self.outcome_model(state, action)
            for label, p in dist.outcomes.items():
                next_state = self._next(state, action, label)
                utility = self._value(next_state, [label], actions, depth - 1)
                trajectories.append(
                    Trajectory(
                        states=[state, next_state],
                        probability=p,
                        utility=utility,
                        actions_taken=[action],
                    )
                )
        return trajectories


class AdversaryModel(ABC):
    """Modelo del oponente para dominios competitivos (minimax / teoría de juegos).

    Contrato: dado un estado, predice la distribución de respuestas del adversario
    asumiendo que este también optimiza — nunca asumir un oponente pasivo.
    """

    @abstractmethod
    def respond(self, state: WorldState, our_action: Action) -> list[tuple[Action, float]]:
        """Acciones probables del adversario ante `our_action`, con probabilidades."""
        ...


class SoftmaxAdversary(AdversaryModel):
    """Adversario racional-con-ruido: responde según softmax de SU utilidad.

    `temperature` → 0 se acerca a best-response puro (minimax); alta = más
    errático. Modelar al oponente como imperfectamente racional es más honesto
    que asumir optimalidad perfecta o pasividad.
    """

    def __init__(
        self,
        candidate_actions: list[Action],
        their_utility: Callable[[WorldState, Action, Action], float],
        temperature: float = 1.0,
    ) -> None:
        if not candidate_actions:
            raise ValueError("El adversario necesita acciones candidatas")
        if temperature <= 0:
            raise ValueError("temperature debe ser > 0")
        self.candidate_actions = candidate_actions
        self.their_utility = their_utility
        self.temperature = temperature

    def respond(self, state: WorldState, our_action: Action) -> list[tuple[Action, float]]:
        scores = [self.their_utility(state, our_action, a) for a in self.candidate_actions]
        top = max(scores)
        exps = [math.exp((s - top) / self.temperature) for s in scores]
        total = sum(exps)
        return [(a, e / total) for a, e in zip(self.candidate_actions, exps)]
