"""Configuración, registro de proveedores y persistencia SQLite."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from oraqlo.config import load_config
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.memory.calibration import CalibrationLedger, multiclass_brier
from oraqlo.store import Store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OLLAMA_API_KEY", "ORAQLO_CONFIG"):
        monkeypatch.delenv(var, raising=False)


def _cfg(tmp_path: Path, toml: str):
    path = tmp_path / "oraqlo.toml"
    path.write_text(toml, encoding="utf-8")
    return load_config(path, home=tmp_path)


# ── configuración ─────────────────────────────────────────────────────────


def test_roles_heredan_de_default(tmp_path):
    c = _cfg(
        tmp_path,
        """
[roles.default]
provider = "anthropic"
model = "claude-opus-5-5"
effort = "medium"
[roles.juez]
effort = "high"
[roles.red_team]
provider = "groq"
""",
    )
    juez = c.role("juez")
    assert (juez.provider, juez.model, juez.effort) == ("anthropic", "claude-opus-5-5", "high")
    rt = c.role("red_team")  # otro proveedor: no hereda el modelo de Claude
    assert rt.provider == "groq" and rt.model == "llama-3.3-70b-versatile"
    assert rt.temperature == 0.9


def test_sin_config_detecta_por_claves(tmp_path, monkeypatch):
    c = load_config(tmp_path / "no-existe.toml", home=tmp_path)
    assert c.role("oraculo").provider == "ollama-local"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert c.role("oraculo").provider == "anthropic"
    assert c.role("oraculo").model == "claude-opus-5-5"


def test_rol_desconocido_falla(tmp_path):
    with pytest.raises(ValueError, match="Roles desconocidos"):
        _cfg(tmp_path, "[roles.inventado]\nmodel = 'x'\n")


def test_registro_cachea_por_effort_y_admite_propios(tmp_path):
    from oraqlo.llm.claude import AnthropicProvider
    from oraqlo.llm.openai_compat import OpenAICompatProvider
    from oraqlo.llm.registry import ProviderRegistry, provider_info

    c = _cfg(
        tmp_path,
        """
[roles.default]
provider = "anthropic"
effort = "low"
[roles.juez]
effort = "max"
[providers.mi-vllm]
type = "openai"
base_url = "http://10.0.0.5:8000/v1"
api_key_env = "MI_KEY"
""",
    )
    reg = ProviderRegistry(c)
    juez, _ = reg.for_role("juez")
    oraculo, _ = reg.for_role("oraculo")
    assert isinstance(juez, AnthropicProvider) and juez.effort == "max"
    assert oraculo.effort == "low" and oraculo is not juez
    assert reg.for_role("oraculo")[0] is oraculo  # cacheado
    custom = reg.get("mi-vllm")
    assert isinstance(custom, OpenAICompatProvider) and custom.base_url.startswith("http://10.")
    assert provider_info("mi-vllm", c).key_present is False
    assert "mi-vllm" in reg.names()


# ── calibración: etiquetas ───────────────────────────────────────────────


def _forecast(fid="f1", outcomes=None, source="test"):
    return Forecast(
        id=fid,
        question="¿sube?",
        distribution=Distribution(outcomes=outcomes or {"Sí": 0.7, "No": 0.3}),
        horizon=timedelta(days=30),
        source=source,
    )


def test_resolver_ignora_mayusculas_y_espacios():
    ledger = CalibrationLedger()
    ledger.record(_forecast())
    r = ledger.resolve("f1", "  sí ")
    assert r.actual_outcome == "Sí"
    assert r.brier == pytest.approx(multiclass_brier({"Sí": 0.7, "No": 0.3}, "Sí"))


# ── SQLite ────────────────────────────────────────────────────────────────


def test_ledger_sqlite_persiste_entre_aperturas(tmp_path):
    db = tmp_path / "o.db"
    s = Store(db)
    s.ledger.record(_forecast("a", source="llm"))
    s.ledger.record(_forecast("b", source="llm"))
    s.ledger.record(_forecast("c"))
    s.ledger.resolve("a", "Sí")
    s.ledger.discard("c")
    s.close()

    s2 = Store(db)
    assert [f.id for f in s2.ledger.open_forecasts] == ["b"]
    assert [r.forecast.id for r in s2.ledger.resolved_forecasts] == ["a"]
    assert s2.ledger.reliability("llm") == pytest.approx(0.18)
    s2.ledger.delete_resolved("a")
    s2.close()
    assert Store(db).ledger.reliability("llm") is None


def test_goals_y_settings_sqlite(tmp_path):
    from oraqlo.goaltrack import GoalTracker
    from test_goaltrack import _plan

    s = Store(tmp_path / "o.db")
    tracked = GoalTracker(s.goals, s.ledger).track(_plan())
    s.settings.set("practice_auto", True)
    s.close()

    s2 = Store(tmp_path / "o.db")
    goal = s2.goals.get(tracked.id)
    assert goal.goal == "maratón sub-4h" and len(goal.milestones) == 2
    assert s2.settings.get("practice_auto") is True
    assert s2.settings.get("nada", 5) == 5
    s2.goals.delete(tracked.id)
    assert s2.goals.list_all() == []


def test_migraciones_y_backup(tmp_path):
    from oraqlo.store.db import MIGRATIONS, Database

    db = Database(tmp_path / "o.db")
    assert db.version == len(MIGRATIONS)
    assert db.migrate() == 0  # idempotente
    Store(tmp_path / "o.db").ledger.record(_forecast())
    copy = db.backup_to(tmp_path / "bk" / "copia.db")
    assert [f.id for f in Store(copy).ledger.open_forecasts] == ["f1"]


def test_base_de_version_futura_se_rechaza(tmp_path):
    from oraqlo.store.db import Database

    db = Database(tmp_path / "o.db")
    db.conn.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="más nueva"):
        Database(tmp_path / "o.db")


def test_import_legacy_idempotente_y_conserva_brier(tmp_path):
    from oraqlo.goaltrack import GoalStore, GoalTracker
    from oraqlo.memory.store import JsonLedger
    from oraqlo.store.db import Database
    from oraqlo.store.legacy import import_legacy
    from test_goaltrack import _plan

    legacy = tmp_path / "legacy"
    jl = JsonLedger(legacy / "oraqlo_ledger.json")
    jl.record(_forecast("x", source="llm"))
    jl.record(_forecast("y", source="llm"))
    jl.resolve("x", "No")
    GoalTracker(GoalStore(legacy / "oraqlo_goals.json"), jl).track(_plan())
    (legacy / "oraqlo_settings.json").write_text(json.dumps({"practice_auto": True}), "utf-8")

    db = Database(tmp_path / "o.db")
    first = import_legacy(db, legacy)
    assert (first.open_forecasts, first.resolved_forecasts, first.goals, first.settings) == (
        1,
        1,
        1,
        1,
    )
    second = import_legacy(db, legacy)
    assert (second.open_forecasts, second.resolved_forecasts, second.goals) == (0, 0, 0)

    store = Store(tmp_path / "o.db")
    assert store.ledger.reliability("llm") == pytest.approx(jl.reliability("llm"))
