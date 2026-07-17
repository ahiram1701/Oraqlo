"""Panel de decisión: debate interno multi-rol antes de emitir una estrategia.

Cada rol es un LLM (posiblemente distinto modelo/backend, ver config/roles.example.yaml).
Orden del debate: Estratega propone → Red Team ataca → Oráculo puntúa → Juez sintetiza.
Ninguna estrategia sale sin pasar por el Red Team: principio de auto-crítica adversarial.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from oraqlo.llm.base import ChatMessage, LLMProvider
from oraqlo.strategy.planner import Recommendation

# Callback de progreso: recibe (rol, texto) al terminar cada turno del debate.
OnTurn = Callable[[str, str], None]

# Prompts de sistema por rol. Deliberadamente cortos: el contexto del caso
# (world model, forecasts, simulaciones) se inyecta como mensajes de usuario.
ROLE_PROMPTS: dict[str, str] = {
    "estratega": (
        "Eres el Estratega. Propón el mejor plan de acción dado el contexto, con una "
        "tesis clara de por qué funcionará. Enumera tus supuestos explícitamente."
    ),
    "red_team": (
        "Eres el Red Team. Tu único trabajo es refutar el plan propuesto: encuentra "
        "supuestos frágiles, modos de fallo, y cómo un adversario competente lo "
        "explotaría. No propongas alternativas; solo ataca. "
        'Responde SOLO con JSON: {"objections": ["<objeción concreta>", ...]} '
        "(entre 2 y 5 objeciones, las más letales primero)."
    ),
    "oraculo": (
        "Eres el Oráculo. Asigna probabilidades numéricas en [0,1] a: (a) que el plan "
        "logre su objetivo, (b) que cada objeción del Red Team se materialice. "
        "Nunca des certezas; calibra tu confianza. Responde SOLO con JSON: "
        '{"p_exito": <prob>, "p_objeciones": {"<objeción>": <prob>, ...}}'
    ),
    "juez": (
        "Eres el Juez. Sintetiza el debate y decide. Responde SOLO con JSON: "
        '{"decision": "emitir"|"revisar"|"descartar", "confidence": <prob en [0,1]>, '
        '"rationale": "<síntesis en 2-3 frases>", '
        '"review_triggers": ["<condición observable que invalidaría la decisión>", ...]}'
    ),
}

DEBATE_ORDER = ("estratega", "red_team", "oraculo", "juez")
VALID_DECISIONS = ("emitir", "revisar", "descartar")


class PanelError(RuntimeError):
    """Un rol del panel no produjo salida utilizable tras los reintentos."""


def _extract_json(text: str) -> dict:
    """Extrae el primer objeto JSON del texto (tolera prosa alrededor)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"Sin objeto JSON en la respuesta: {text[:200]!r}")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("El JSON raíz no es un objeto")
    return data


@dataclass
class RoleConfig:
    """Un rol del panel: qué proveedor/modelo lo encarna y con qué temperatura."""

    name: str
    provider: LLMProvider
    model: str
    temperature: float = 0.7


@dataclass
class Verdict:
    """Resultado del debate del panel."""

    decision: str  # "emitir" | "revisar" | "descartar"
    confidence: float
    rationale: str
    objections: list[str] = field(default_factory=list)
    p_success: float | None = None
    review_triggers: list[str] = field(default_factory=list)
    transcript: list[tuple[str, str]] = field(default_factory=list)  # (rol, texto)


class DecisionPanel(ABC):
    """Contrato del panel: somete una recomendación al debate y emite un veredicto.

    El transcript completo se conserva siempre: trazabilidad es un principio
    de diseño, no una opción.
    """

    def __init__(self, roles: dict[str, RoleConfig]) -> None:
        missing = set(ROLE_PROMPTS) - set(roles)
        if missing:
            raise ValueError(f"Faltan roles en el panel: {sorted(missing)}")
        self.roles = roles

    @abstractmethod
    def deliberate(
        self, context: str, candidate: Recommendation, on_turn: OnTurn | None = None
    ) -> Verdict:
        """Somete la recomendación candidata al debate y devuelve el veredicto.

        `on_turn(rol, texto)` se invoca al completarse cada turno (para UIs que
        muestran el debate en vivo). Sin callback, el comportamiento es idéntico.
        """
        ...


def _describe(candidate: Recommendation) -> str:
    lines = [
        f"Acción propuesta: {candidate.action.description}",
        f"Utilidad esperada: {candidate.expected_utility:.3f} "
        f"(ajustada a riesgo: {candidate.risk_adjusted_utility:.3f})",
    ]
    if candidate.assumptions:
        lines.append("Supuestos del planificador: " + "; ".join(candidate.assumptions))
    return "\n".join(lines)


class SequentialPanel(DecisionPanel):
    """Debate secuencial: cada rol ve lo dicho por los anteriores.

    Los roles con salida estructurada (red_team, oraculo, juez) usan json_mode
    y reintento con feedback: si el JSON es inválido, se le muestra el error al
    modelo y se le pide solo el JSON corregido (una vez).
    """

    def __init__(self, roles: dict[str, RoleConfig], max_retries: int = 1) -> None:
        super().__init__(roles)
        self.max_retries = max_retries

    def _speak(self, role: str, prompt: str, json_mode: bool) -> str:
        cfg = self.roles[role]
        messages = [
            ChatMessage(role="system", content=ROLE_PROMPTS[role]),
            ChatMessage(role="user", content=prompt),
        ]
        response = cfg.provider.chat(
            messages=messages, model=cfg.model, temperature=cfg.temperature, json_mode=json_mode
        )
        return response.content

    def _speak_json(self, role: str, prompt: str) -> dict:
        cfg = self.roles[role]
        messages = [
            ChatMessage(role="system", content=ROLE_PROMPTS[role]),
            ChatMessage(role="user", content=prompt),
        ]
        last_error: Exception | None = None
        for _ in range(self.max_retries + 1):
            response = cfg.provider.chat(
                messages=messages, model=cfg.model, temperature=cfg.temperature, json_mode=True
            )
            try:
                return _extract_json(response.content)
            except (ValueError, json.JSONDecodeError) as e:
                last_error = e
                messages.append(ChatMessage(role="assistant", content=response.content))
                messages.append(
                    ChatMessage(
                        role="user",
                        content=f"Tu respuesta no es válida ({e}). Devuelve SOLO el JSON corregido.",
                    )
                )
        raise PanelError(f"El rol '{role}' ({cfg.model}) no produjo JSON válido: {last_error}")

    def deliberate(
        self, context: str, candidate: Recommendation, on_turn: OnTurn | None = None
    ) -> Verdict:
        transcript: list[tuple[str, str]] = []

        def turn(role: str, text: str) -> None:
            transcript.append((role, text))
            if on_turn is not None:
                on_turn(role, text)

        base = f"Contexto del caso:\n{context}\n\nRecomendación candidata:\n{_describe(candidate)}"

        # 1. Estratega: articula el plan y su tesis (texto libre, es argumentación).
        plan = self._speak(
            "estratega",
            f"{base}\n\nArticula el plan final: tesis, pasos clave y supuestos explícitos.",
            json_mode=False,
        )
        turn("estratega", plan)

        # 2. Red Team: ataca el plan.
        rt = self._speak_json("red_team", f"{base}\n\nPlan del Estratega:\n{plan}\n\nAtácalo.")
        objections = [str(o) for o in rt.get("objections", []) if str(o).strip()]
        if not objections:
            raise PanelError("El Red Team no produjo objeciones: el debate no es válido sin ataque.")
        turn("red_team", json.dumps({"objections": objections}, ensure_ascii=False))

        # 3. Oráculo: probabilidades de éxito y de cada objeción.
        oracle = self._speak_json(
            "oraculo",
            f"{base}\n\nPlan del Estratega:\n{plan}\n\nObjeciones del Red Team:\n"
            + "\n".join(f"- {o}" for o in objections),
        )
        p_success = oracle.get("p_exito")
        p_success = min(max(float(p_success), 0.0), 1.0) if p_success is not None else None
        turn("oraculo", json.dumps(oracle, ensure_ascii=False))

        # 4. Juez: sintetiza y decide.
        judge = self._speak_json(
            "juez",
            f"{base}\n\nPlan:\n{plan}\n\nObjeciones:\n"
            + "\n".join(f"- {o}" for o in objections)
            + f"\n\nProbabilidades del Oráculo: {json.dumps(oracle, ensure_ascii=False)}",
        )
        decision = str(judge.get("decision", "")).strip().lower()
        if decision not in VALID_DECISIONS:
            raise PanelError(
                f"El Juez emitió una decisión inválida: {decision!r} (esperado: {VALID_DECISIONS})"
            )
        confidence = min(max(float(judge.get("confidence", 0.0)), 0.0), 1.0)
        turn("juez", json.dumps(judge, ensure_ascii=False))

        return Verdict(
            decision=decision,
            confidence=confidence,
            rationale=str(judge.get("rationale", "")),
            objections=objections,
            p_success=p_success,
            review_triggers=[str(t) for t in judge.get("review_triggers", [])],
            transcript=transcript,
        )
