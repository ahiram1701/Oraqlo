"""Notificaciones Web Push (VAPID) a los dispositivos suscritos.

Las claves VAPID se generan una vez y se guardan en la base local. Los avisos
llevan un `url` que la PWA abre al tocar la notificación. Las suscripciones que
el servicio de push da por muertas (404/410) se borran solas.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from oraqlo.store.db import Database, dumps, loads
from oraqlo.store.settings import SettingsStore

log = logging.getLogger(__name__)

# Identificador del emisor exigido por los servicios de push (no es un dato personal).
VAPID_SUBJECT = "https://github.com/ahiram1701/Oraqlo"


class PushService:
    def __init__(self, db: Database, settings: SettingsStore) -> None:
        self.db = db
        self.settings = settings

    # ── claves ────────────────────────────────────────────────────────────

    def _keys(self) -> tuple[str, str]:
        keys = self.settings.get("push.vapid")
        if keys:
            return keys["private_pem"], keys["public_key"]
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid01

        vapid = Vapid01()
        vapid.generate_keys()
        private_pem = vapid.private_pem().decode()
        assert vapid.public_key is not None  # generate_keys() siempre la crea
        raw = vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        public_key = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        self.settings.set("push.vapid", {"private_pem": private_pem, "public_key": public_key})
        return private_pem, public_key

    def public_key(self) -> str:
        return self._keys()[1]

    # ── suscripciones ─────────────────────────────────────────────────────

    def subscribe(self, subscription: dict[str, Any], user_login: str | None) -> None:
        endpoint = subscription.get("endpoint")
        keys = subscription.get("keys") or {}
        if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
            raise ValueError("Suscripción push inválida (faltan endpoint o claves).")
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO push_subscriptions (endpoint, data, user_login) VALUES (?, ?, ?) "
                "ON CONFLICT(endpoint) DO UPDATE SET data = excluded.data, user_login = excluded.user_login",
                (endpoint, dumps(subscription), user_login),
            )

    def unsubscribe(self, endpoint: str) -> None:
        with self.db.tx() as conn:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))

    def count(self) -> int:
        return int(self.db.query("SELECT COUNT(*) FROM push_subscriptions")[0][0])

    # ── envío ─────────────────────────────────────────────────────────────

    def send(self, title: str, body: str, url: str = "/", tag: str | None = None) -> int:
        """Envía a todos los dispositivos. Devuelve cuántos lo recibieron."""
        from pywebpush import WebPushException, webpush

        rows = self.db.query("SELECT endpoint, data FROM push_subscriptions")
        if not rows:
            return 0
        private_pem, _ = self._keys()
        payload = json.dumps(
            {"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False
        )
        delivered = 0
        for row in rows:
            try:
                webpush(
                    subscription_info=loads(row["data"]),
                    data=payload,
                    vapid_private_key=private_pem,
                    vapid_claims={"sub": VAPID_SUBJECT},
                    timeout=10,
                    ttl=24 * 3600,
                )
                delivered += 1
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                if status in (404, 410):
                    self.unsubscribe(row["endpoint"])
                else:
                    log.warning("Push fallido (%s): %s", status, e)
            except Exception as e:
                log.warning("Push fallido: %s", e)
        return delivered


JOB_TITLES = {
    "forecast": "Previsión lista",
    "full": "Análisis completo listo",
    "case": "Análisis listo",
    "goal": "Plan de objetivo listo",
    "checkin": "Check-in evaluado",
    "replan": "Plan re-diseñado",
}


def job_notification(job: dict) -> tuple[str, str, str] | None:
    """(título, cuerpo, url) para un job terminado; None si no merece aviso."""
    kind, status = job.get("kind"), job.get("status")
    if kind not in JOB_TITLES:
        return None
    params = job.get("params") or {}
    subject = str(params.get("question") or params.get("goal") or "")[:90]
    if status == "failed":
        return "Oraqlo: algo falló", f"{subject} — {job.get('error') or 'error'}"[:180], "/"
    if status != "done":
        return None
    result = job.get("result") or {}
    url = f"/historial/{job['run_id']}" if job.get("run_id") else "/"
    if kind == "goal":
        url = f"/objetivos/{result.get('goal_id')}"
        up = result.get("uplift", 0.0)
        body = f"{subject}: {result.get('baseline_p', 0):.0%} → {result.get('with_plan_p', 0):.0%} ({up:+.0%})"
    elif kind in ("checkin", "replan"):
        goal = result.get("goal") or {}
        url = f"/objetivos/{goal.get('id', '')}"
        body = f"P(objetivo) ahora {goal.get('current_p', 0):.0%}"
    elif kind == "forecast":
        outcomes = (result.get("forecast") or {}).get("outcomes") or {}
        top = next(iter(outcomes.items()), None)
        body = f"{subject}: {top[0]} {top[1]:.0%}" if top else subject
    else:
        verdict = result.get("verdict") or {}
        body = f"{subject}: {verdict.get('decision', '—')} ({verdict.get('confidence', 0):.0%})"
    return JOB_TITLES[kind], body, url
