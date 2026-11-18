"""Estimación por partes (estilo Fermi) para preguntas de sí/no compuestas.

«¿Abrirá la tienda y será rentable antes de junio?» depende de varias cosas a la
vez. Estimar la cifra de golpe suele salir demasiado alto: se ve la historia
completa y se olvida que cada eslabón puede fallar. Aquí:

1. Un modelo decide si la pregunta se descompone de verdad y cómo:
   - «y»: hace falta que pasen TODAS (cadena: P = p1 · p2|1 · p3|1,2 …);
   - «o»: basta con que pase UNA (P = 1 − Π(1 − pᵢ|ninguna anterior)).
2. Otro modelo (si hay comité) estima cada parte, condicionada a las anteriores.
3. La cifra resultante entra como la voz `estructura` en la combinación, con un peso
   que aprende de su historial como cualquier miembro del comité.

Si la pregunta es simple, no se descompone: no se gasta nada más.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from oraqlo.forecaster.aggregate import clamp_probability
from oraqlo.llm.base import LLMProvider
from oraqlo.llm.structured import JsonChatError, chat_json

MAX_PARTS = 4

_SPLIT_PROMPT = (
    "Eres el Analista de Oraqlo. Decide si esta pregunta de sí/no es COMPUESTA: si su "
    "respuesta depende de varias cosas distintas que se pueden estimar por separado. "
    "Responde SOLO con JSON:\n"
    '{"compound": false} si es simple, o\n'
    '{"compound": true, "mode": "y"|"o", "yes_label": "<el escenario que significa que '
    'SÍ pasa, copiado tal cual>", "parts": ["<subpregunta de sí/no>", ...]}\n'
    "«y»: hacen falta TODAS las partes; «o»: basta con UNA. Entre 2 y 4 partes, en orden "
    "lógico (lo primero que tendría que pasar, primero), concretas y en el idioma de la "
    "pregunta. No inventes partes de relleno."
)

_ESTIMATE_PROMPT = (
    "Eres el Oráculo de Oraqlo: probabilidades calibradas, nunca certezas. Estima cada "
    "parte EN ORDEN y CONDICIONADA a las anteriores, como se indica. Parte de la tasa base "
    "de cada una y evita 0 y 1. Responde SOLO con JSON: "
    '{"p": [<prob parte 1>, <prob parte 2>, ...], "rationale": "<una frase>"}'
)


@dataclass
class Decomposition:
    mode: str  # "y" | "o"
    yes_label: str
    parts: list[str]
    probabilities: list[float]
    rationale: str = ""

    @property
    def p_yes(self) -> float:
        if self.mode == "y":
            p = 1.0
            for q in self.probabilities:
                p *= q
            return p
        none = 1.0
        for q in self.probabilities:
            none *= 1.0 - q
        return 1.0 - none

    def outcomes(self, labels: list[str]) -> dict[str, float]:
        p = clamp_probability(self.p_yes)
        no = next(label for label in labels if label != self.yes_label)
        return {self.yes_label: p, no: 1.0 - p}

    def note(self) -> str:
        joiner = " y luego " if self.mode == "y" else " o bien "
        parts = joiner.join(
            f"«{t}» ({p:.0%})" for t, p in zip(self.parts, self.probabilities, strict=True)
        )
        rule = "hacen falta todas" if self.mode == "y" else "basta con una"
        return f"Por partes ({rule}): {parts} → {self.p_yes:.0%}."

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "yes_label": self.yes_label,
            "parts": [
                {"text": t, "p": p} for t, p in zip(self.parts, self.probabilities, strict=True)
            ],
            "p_yes": self.p_yes,
            "rationale": self.rationale,
        }


def _norm(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def decompose(
    question: str,
    horizon: timedelta,
    labels: list[str],
    splitter: tuple[LLMProvider, str],
    estimator: tuple[LLMProvider, str],
    context: str = "",
) -> Decomposition | None:
    """La estimación por partes, o None si la pregunta es simple (o algo falla)."""
    if len(labels) != 2:
        return None
    header = (
        f"Fecha de hoy: {date.today().isoformat()}\nPregunta: {question}\n"
        f"Horizonte: {horizon.days} días\nEscenarios: {labels}"
    )
    try:
        split = chat_json(splitter[0], splitter[1], _SPLIT_PROMPT, header, temperature=0.2)
    except JsonChatError:
        return None
    if not split.get("compound"):
        return None
    mode = str(split.get("mode", "")).strip().lower()
    by_norm = {_norm(label): label for label in labels}
    yes_label = by_norm.get(_norm(split.get("yes_label", "")))
    parts = [str(p).strip() for p in split.get("parts") or [] if str(p).strip()][:MAX_PARTS]
    if mode not in ("y", "o") or yes_label is None or len(parts) < 2:
        return None
    if mode == "y":
        how = "La parte N se estima SUPONIENDO que las anteriores SÍ pasaron."
    else:
        how = "La parte N se estima SUPONIENDO que ninguna de las anteriores pasó."
    listing = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(parts))
    user = f"{header}\n\nPartes:\n{listing}\n\n{how}"
    if context:
        user += f"\n\n{context}"
    try:
        est = chat_json(estimator[0], estimator[1], _ESTIMATE_PROMPT, user, temperature=0.2)
        probs = [clamp_probability(float(p)) for p in est.get("p") or []]
    except (JsonChatError, TypeError, ValueError):
        return None
    if len(probs) != len(parts):
        return None
    return Decomposition(mode, yes_label, parts, probs, str(est.get("rationale") or "").strip())
