"""Diagnóstico: ¿está todo listo para usar Oraqlo? Lo usan `oraqlo doctor` y la app.

Cada comprobación dice qué pasa y, si falla, qué hacer, en palabras de la app
(no en comandos): el usuario lo lee en Ajustes > Diagnóstico.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from oraqlo.config import ROLES, OraqloConfig
from oraqlo.llm.registry import ProviderRegistry, provider_info
from oraqlo.llm.websearch import OllamaWebClient

WEB_SEARCH_CHECK = "Investigación web"
WEB_SEARCH_FIX = "Pega tu clave de ollama.com en Ajustes > Búsqueda web."


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str | None = None
    # Una función opcional que falta (p. ej. la búsqueda web) se avisa, pero no hace
    # que el diagnóstico entero quede "con fallos".
    optional: bool = False

    @property
    def blocking(self) -> bool:
        return not self.ok and not self.optional

    def as_dict(self) -> dict:
        return asdict(self)


def probe_provider(registry: ProviderRegistry, name: str) -> Check:
    """Prueba real (barata): listar modelos con la clave actual."""
    info = provider_info(name, registry.config)
    if info.key_present is False:
        return Check(
            name,
            False,
            f"Falta la clave ({info.api_key_env}).",
            "Pégala en Ajustes > Proveedores.",
        )
    try:
        models = registry.get(name).list_models()
    except Exception as e:  # cualquier fallo del backend se cuenta, no se lanza
        msg = str(e) or type(e).__name__
        fix = (
            "Abre Ollama en este PC (o instálalo desde ollama.com)."
            if name == "ollama-local"
            else "Revisa que la clave sea correcta y tenga saldo."
        )
        return Check(name, False, f"No responde: {msg[:300]}", fix)
    if info.kind == "ollama" and models:
        have = set(models) | {m.removesuffix(":latest") for m in models}
        config = registry.config
        missing = sorted(
            {config.role(r).model for r in ROLES if config.role(r).provider == name} - have
        )
        if missing:
            return Check(
                name,
                False,
                f"Responde, pero no tiene el modelo {', '.join(missing)}.",
                "Elige uno que tengas en Ajustes > Modelos del panel.",
            )
    n = len(models)
    detail = (
        f"Responde ({n} modelo{'' if n == 1 else 's'} disponible{'' if n == 1 else 's'})."
        if n
        else "Responde."
    )
    return Check(name, True, detail)


def web_search_check(web: OllamaWebClient | None = None) -> Check:
    """Sin gastar cuota: solo mira si hay clave. La búsqueda es opcional, así que su
    ausencia no es un fallo grave, pero se avisa para que el usuario sepa activarla."""
    web = web or OllamaWebClient()
    if web.is_available():
        return Check(WEB_SEARCH_CHECK, True, "Clave presente: Oraqlo puede investigar en internet.")
    return Check(
        WEB_SEARCH_CHECK,
        False,
        f"Sin clave ({web.api_key_env}): las preguntas se responden sin buscar en internet.",
        WEB_SEARCH_FIX,
        optional=True,
    )


def probe_web_search(web: OllamaWebClient | None = None) -> Check:
    """Prueba real: una búsqueda mínima con la clave actual."""
    web = web or OllamaWebClient()
    if not web.is_available():
        return web_search_check(web)
    try:
        web.search("Oraqlo", max_results=1)
    except Exception as e:  # el error de OllamaUnavailableError ya es legible
        return Check(
            WEB_SEARCH_CHECK, False, str(e) or type(e).__name__, WEB_SEARCH_FIX, optional=True
        )
    return Check(WEB_SEARCH_CHECK, True, "La búsqueda web responde.")


def run_checks(
    config: OraqloConfig,
    store=None,
    ping: bool = True,
    registry: ProviderRegistry | None = None,
) -> list[Check]:
    checks: list[Check] = [Check("Configuración", True, f"Cargada desde {config.home}.")]
    if store is not None:
        try:
            checks.append(
                Check(
                    "Base de datos",
                    True,
                    f"v{store.db.version}: {len(store.ledger.open_forecasts)} predicciones "
                    f"abiertas, {len(store.ledger.resolved_forecasts)} resueltas, "
                    f"{len(store.goals.list_all())} objetivos.",
                )
            )
        except Exception as e:
            checks.append(Check("Base de datos", False, str(e), "Reinicia Oraqlo."))
    registry = registry or ProviderRegistry(config)
    for name in sorted({config.role(r).provider for r in ROLES}):
        info = provider_info(name, config)
        if not ping:
            ok = info.key_present is not False
            detail = (
                "Clave presente."
                if info.key_present
                else ("No necesita clave." if ok else f"Falta {info.api_key_env}.")
            )
            checks.append(
                Check(name, ok, detail, None if ok else "Pégala en Ajustes > Proveedores.")
            )
        else:
            checks.append(probe_provider(registry, name))
    checks.append(web_search_check())
    return checks
