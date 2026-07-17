"""Informes en Markdown: el rastro auditable de una ejecución, exportable y archivable.

Un informe sin el debate y los supuestos no es auditable; aquí se vuelca todo.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from typing import TYPE_CHECKING

from oraqlo.forecaster.base import Forecast
from oraqlo.panel import Verdict
from oraqlo.strategy.planner import Recommendation

if TYPE_CHECKING:
    from oraqlo.goals import GoalPlan
    from oraqlo.goaltrack import TrackedGoal


def slugify(text: str, max_len: int = 40) -> str:
    """Slug seguro para nombres de archivo."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "caso"


def _pretty_turn(role: str, text: str) -> str:
    """Los roles JSON (red_team/oraculo/juez) se muestran como bloque de código."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            pretty = json.dumps(json.loads(stripped), ensure_ascii=False, indent=2)
            return f"```json\n{pretty}\n```"
        except json.JSONDecodeError:
            pass
    return stripped


def render_report_markdown(
    question: str,
    forecast: Forecast,
    ranking: list[Recommendation],
    verdict: Verdict | None,
    model: str,
    backend: str,
    research_facts: list[tuple[str, str]] | None = None,
) -> str:
    """`research_facts`: pares (hecho, url) del investigador web, si hubo investigación."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# Informe estratégico de Oraqlo",
        "",
        f"- **Pregunta:** {question}",
        f"- **Fecha:** {now}",
        f"- **Modelo:** {model} @ {backend}",
        f"- **Horizonte:** {forecast.horizon.days} días",
        f"- **Id de predicción (ledger):** `{forecast.id}`",
        "",
        "## Previsión del oráculo",
        "",
        "| Escenario | Probabilidad |",
        "|---|---|",
    ]
    for label, p in sorted(forecast.distribution.outcomes.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {label} | {p:.1%} |")
    if forecast.assumptions:
        lines += ["", "**Supuestos del oráculo:**", ""]
        lines += [f"- {a}" for a in forecast.assumptions]

    if research_facts:
        lines += ["", "## Fuentes consultadas (investigación web)", ""]
        lines += [f"- {text} — <{url}>" for text, url in research_facts]

    lines += ["", "## Ranking del planificador", "",
              "| Acción | Utilidad esperada | Ajustada a riesgo |", "|---|---|---|"]
    for rec in ranking:
        lines.append(
            f"| {rec.action.id} | {rec.expected_utility:+.3f} | {rec.risk_adjusted_utility:+.3f} |"
        )
    if ranking and ranking[0].rejected_alternatives:
        lines += ["", "**Descartadas por el planificador:**", ""]
        lines += [f"- {action.id}: {reason}" for action, reason in ranking[0].rejected_alternatives]

    if verdict is not None:
        lines += [
            "",
            "## Veredicto del panel",
            "",
            f"**Decisión: {verdict.decision.upper()}** · confianza del Juez: {verdict.confidence:.0%}"
            + (f" · P(éxito) según el Oráculo: {verdict.p_success:.0%}"
               if verdict.p_success is not None else ""),
            "",
            verdict.rationale,
        ]
        if verdict.objections:
            lines += ["", "**Objeciones del Red Team:**", ""]
            lines += [f"- {o}" for o in verdict.objections]
        if verdict.review_triggers:
            lines += ["", "**Disparadores de revisión** (si se observan, esta decisión caduca):", ""]
            lines += [f"- {t}" for t in verdict.review_triggers]
        if verdict.transcript:
            lines += ["", "## Transcript del debate"]
            for role, text in verdict.transcript:
                lines += ["", f"### {role}", "", _pretty_turn(role, text)]

    lines += [
        "",
        "---",
        "",
        "*Cuando el mundo responda, cierra la predicción en el ledger con el resultado real "
        "para alimentar la calibración.*",
        "",
    ]
    return "\n".join(lines)


def render_goal_report_markdown(
    plan: "GoalPlan",
    model: str,
    backend: str,
    research_facts: list[tuple[str, str]] | None = None,
    tracked: "TrackedGoal | None" = None,
) -> str:
    """Informe del modo objetivo: línea base, palancas, plan, hitos, debate y uplift."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sign = "+" if plan.uplift >= 0 else ""
    lines: list[str] = [
        "# Plan de objetivo de Oraqlo",
        "",
        f"- **Objetivo:** {plan.goal}",
        f"- **Fecha:** {now}",
        f"- **Modelo:** {model} @ {backend}",
        f"- **Horizonte:** {plan.horizon.days} días",
        "",
        "## Probabilidades",
        "",
        f"| | P(objetivo) |",
        f"|---|---|",
        f"| Sin cambiar nada (statu quo) | {plan.baseline_p:.0%} |",
        f"| Ejecutando el plan | {plan.with_plan_p:.0%} |",
        f"| **Uplift** | **{sign}{plan.uplift:.0%}** |",
        "",
        f"Ids en el ledger: statu quo `{plan.baseline.id}` · con plan `{plan.with_plan.id}`",
    ]
    if plan.uplift <= 0:
        lines += ["", "> ⚠️ El plan NO aumenta la probabilidad estimada del objetivo. "
                      "Revísalo antes de invertir esfuerzo en él."]

    lines += ["", "## Qué puedes hacer (palancas controlables)", "",
              "| Palanca | Impacto | Esfuerzo |", "|---|---|---|"]
    lines += [f"| {l.text} | {l.impact} | {l.effort} |" for l in plan.levers]

    if plan.external_conditions:
        lines += ["", "## Qué tiene que pasar (condiciones externas)", "",
                  "| Condición | Probabilidad |", "|---|---|"]
        lines += [f"| {c.text} | {c.probability:.0%} |" for c in plan.external_conditions]

    lines += ["", "## Qué tienes que hacer (el plan)", "", plan.summary, ""]
    for step in plan.steps:
        lines.append(f"{step.order}. **{step.action}** — {step.when}")
        if step.why:
            lines.append(f"   - *Por qué:* {step.why}")

    if plan.milestones:
        lines += ["", "## Hitos (sabrás si vas por buen camino)", "",
                  "| Hito | Fecha objetivo | Señal |", "|---|---|---|"]
        lines += [f"| {m.text} | {m.target_date} | {m.signal} |" for m in plan.milestones]

    if plan.verdict is not None:
        v = plan.verdict
        lines += ["", "## Veredicto del panel", "",
                  f"**{v.decision.upper()}** · confianza {v.confidence:.0%}"
                  + (" · plan re-diseñado tras las objeciones" if plan.revised else ""),
                  "", v.rationale]
        if v.objections:
            lines += ["", "**Objeciones del Red Team:**", ""]
            lines += [f"- {o}" for o in v.objections]
        if v.review_triggers:
            lines += ["", "**Disparadores de revisión:**", ""]
            lines += [f"- {t}" for t in v.review_triggers]

    if tracked is not None:
        lines += ["", "## Seguimiento", "",
                  f"Estado: **{tracked.status}** · plan v{tracked.plan_version} · "
                  f"fecha límite {tracked.deadline} ({tracked.days_left} días restantes)",
                  "",
                  "Trayectoria de P(objetivo): "
                  + " → ".join(f"{p:.0%}" for p in tracked.trajectory)]
        if tracked.milestones:
            lines += ["", "| Hito | Fecha | Estado |", "|---|---|---|"]
            lines += [
                f"| {m.text} | {m.target_date} | {m.status}"
                + (" ⚠️ vencido" if m.overdue else "") + " |"
                for m in tracked.milestones
            ]
        if tracked.checkins:
            lines += ["", "**Check-ins:**", ""]
            lines += [
                f"- {c.date[:10]} · P={c.p_estimate:.0%} · {c.note}"
                + (f" — *{c.comment}*" if c.comment else "")
                for c in tracked.checkins
            ]

    if research_facts:
        lines += ["", "## Fuentes consultadas (investigación web)", ""]
        lines += [f"- {text} — <{url}>" for text, url in research_facts]

    lines += ["", "---", "",
              "*Las dos predicciones quedan abiertas en el ledger; al vencer el horizonte, "
              "resuélvelas con lo que realmente pasó para que Oraqlo aprenda si sus planes "
              "mueven la probabilidad.*", ""]
    return "\n".join(lines)
