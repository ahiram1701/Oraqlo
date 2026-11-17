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
# Debe ser un mailto: o un https:// SIN ruta: py_vapid rechaza "https://host/ruta" y
# entonces ningún aviso salía (estaba así hasta la 0.5.2).
VAPID_SUBJECT = "https://github.com"


class PushService:
    def __init__(self, db: Database, settings: SettingsStore, desktop=None) -> None:
        # Avisos nativos de Windows (services/notify.DesktopNotifier) además de Web Push.
        self.desktop: Any = desktop
        self.db = db
        self.settings = settings
        self._vapid: Any = None

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

    def desktop_enabled(self) -> bool:
        from oraqlo.services.notify import SETTING

        return bool(
            self.desktop is not None and self.desktop.available and self.settings.get(SETTING, True)
        )

    def count(self) -> int:
        return int(self.db.query("SELECT COUNT(*) FROM push_subscriptions")[0][0])

    # ── envío ─────────────────────────────────────────────────────────────

    def _signer(self) -> Any:
        """Clave VAPID como objeto: pywebpush NO acepta el texto PEM (lo trataría
        como base64 y fallaría con "Could not deserialize key data")."""
        if self._vapid is None:
            from py_vapid import Vapid02  # RFC 8292 ("vapid t=…, k=…"), junto con aes128gcm

            self._vapid = Vapid02.from_pem(self._keys()[0].encode())
        return self._vapid

    def send(self, title: str, body: str, url: str = "/", tag: str | None = None) -> int:
        """Envía a Windows y a todos los dispositivos. Devuelve cuántos lo recibieron."""
        delivered = 0
        if self.desktop_enabled() and self.desktop.send(title, body, url):
            delivered += 1
        return delivered + self.send_web(title, body, url, tag)[0]

    def send_web(
        self, title: str, body: str, url: str = "/", tag: str | None = None
    ) -> tuple[int, str | None]:
        """Solo Web Push. Devuelve (entregados, último error) para poder explicarlo."""
        from pywebpush import WebPushException, webpush

        rows = self.db.query("SELECT endpoint, data FROM push_subscriptions")
        if not rows:
            return 0, "No hay dispositivos suscritos."
        message: dict[str, Any] = {"title": title, "body": body, "url": url}
        if tag:  # un tag nulo se volvería "null" y unos avisos taparían a otros
            message["tag"] = tag
        payload = json.dumps(message, ensure_ascii=False)
        delivered, error = 0, None
        for row in rows:
            try:
                webpush(
                    subscription_info=loads(row["data"]),
                    data=payload,
                    vapid_private_key=self._signer(),
                    vapid_claims={"sub": VAPID_SUBJECT},
                    timeout=10,
                    ttl=24 * 3600,
                )
                delivered += 1
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                if status in (404, 410):
                    self.unsubscribe(row["endpoint"])
                    error = (
                        "Una suscripción caducó y se borró: vuelve a activarlas en ese dispositivo."
                    )
                else:
                    error = f"El servicio de push respondió {status}: {e}"[:300]
                    log.warning("Push fallido (%s): %s", status, e)
            except Exception as e:
                error = f"No pude enviar el aviso: {e}"[:300]
                log.warning("Push fallido: %s", e)
        self.settings.set("push.last_error", None if delivered else error)
        return delivered, (None if delivered else error)

    def is_subscribed(self, endpoint: str) -> bool:
        rows = self.db.query("SELECT 1 FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        return bool(rows)


JOB_TITLES = {
    "forecast": "Previsión lista",
    "full": "Análisis completo listo",
    "case": "Análisis listo",
    "goal": "Plan de objetivo listo",
    "checkin": "Check-in evaluado",
    "replan": "Plan re-diseñado",
}
KIND_NAMES = {**JOB_TITLES, "practice": "Práctica"}


def _subject(job: dict) -> str:
    params = job.get("params") or {}
    raw_case = params.get("case")
    case: dict = raw_case if isinstance(raw_case, dict) else {}
    text = (
        params.get("question") or params.get("goal") or case.get("question") or params.get("note")
    )
    return str(text or KIND_NAMES.get(str(job.get("kind")), "Análisis"))[:90]


def _origin_url(job: dict) -> str:
    """Pantalla desde la que se lanzó el job: ahí se ve el error."""
    params = job.get("params") or {}
    kind = job.get("kind")
    if kind in ("checkin", "replan") and params.get("goal_id"):
        return f"/objetivos/{params['goal_id']}"
    return {"case": "/caso", "practice": "/practica", "goal": "/objetivos"}.get(str(kind), "/")


def job_notification(job: dict) -> tuple[str, str, str] | None:
    """(título, cuerpo, url) para un job terminado; None si no merece aviso."""
    kind, status = job.get("kind"), job.get("status")
    if kind not in JOB_TITLES:
        return None
    subject = _subject(job)
    if status == "failed":
        error = job.get("error") or "error desconocido"
        return "Oraqlo: algo falló", f"{subject} — {error}"[:180], _origin_url(job)
    if status != "done":
        return None
    result = job.get("result") or {}
    if result.get("kind") == "questions":  # pausa: internet no bastó
        questions = result.get("questions") or []
        first = str((questions[0] if questions else {}).get("text", ""))
        n = len(questions)
        body = f"{subject}: {first}" + (f" (y {n - 1} más)" if n > 1 else "")
        return "Oraqlo necesita tu respuesta", body[:180], f"/?job={job.get('id')}"
    url = f"/historial/{job['run_id']}" if job.get("run_id") else _origin_url(job)
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


def practice_alert(job: dict, failures: int) -> tuple[str, str, str] | None:
    """La práctica corre sola cada hora: solo se avisa si falla dos veces seguidas."""
    if job.get("kind") != "practice" or job.get("status") != "failed" or failures != 2:
        return None
    error = job.get("error") or "error desconocido"
    return (
        "La práctica automática está fallando",
        f"Dos pasadas seguidas: {error}"[:180],
        "/practica",
    )
