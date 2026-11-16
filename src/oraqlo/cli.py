"""Punto de entrada único: el comando `oraqlo`.

    oraqlo serve [--port N]        servidor web: API + app (celular vía Tailscale)
    oraqlo version                 muestra la versión instalada
    oraqlo practice [opciones]     autopráctica con Polymarket (ver --help)
    oraqlo update [--apply]        busca / instala la última versión publicada
    oraqlo rollback                vuelve a la versión anterior
    oraqlo config                  rutas y roles efectivos
    oraqlo doctor [--preflight]    diagnóstico (config, base, claves, proveedores)
    oraqlo import-legacy [carpeta] importa los JSON de la versión TUI

Los subcomandos importan sus módulos de forma perezosa para que `oraqlo version`
funcione aunque falten extras opcionales.
"""

from __future__ import annotations

import argparse
import logging
import sys

from oraqlo import __version__


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _cmd_practice(args: argparse.Namespace) -> int:
    from oraqlo.autopractice import main as practice_main

    return practice_main(args.rest)


def _cmd_config(args: argparse.Namespace) -> int:
    from oraqlo.config import ROLES, config_path, load_config

    config = load_config()
    path = config_path(config.home)
    print(f"Directorio de datos: {config.home}")
    print(
        f"Configuración:       {path} ({'existe' if path.exists() else 'no existe: valores por defecto'})"
    )
    print(f"Base de datos:       {config.db_path}")
    print("Roles:")
    for role in ROLES:
        r = config.role(role)
        extra = f" effort={r.effort}" if r.effort else f" T={r.temperature}"
        print(f"  {role:<13} {r.provider}:{r.model}{extra}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnóstico: configuración, claves, base de datos y proveedores en uso."""
    from oraqlo.config import load_config
    from oraqlo.services.doctor import run_checks
    from oraqlo.store import Store

    try:
        config = load_config()
    except Exception as e:  # config inválida: se informa, no se lanza
        print(f"[X] Configuración inválida: {e}")
        return 1
    try:
        store = Store(config.db_path)
    except Exception as e:
        print(f"[X] Base de datos: {e}")
        return 1
    try:
        checks = run_checks(config, store, ping=not args.preflight)
    finally:
        store.close()
    for c in checks:
        print(f"[{'ok' if c.ok else 'X'}] {c.name}: {c.detail}" + (f" {c.fix}" if c.fix else ""))
    # En --preflight (instalador) faltar una clave no es un error: se pide en la app.
    return (
        0
        if all(
            c.ok
            for c in checks
            if not args.preflight or c.name in ("Configuración", "Base de datos")
        )
        else 1
    )


def _cmd_import_legacy(args: argparse.Namespace) -> int:
    from oraqlo.config import load_config
    from oraqlo.store import Database
    from oraqlo.store.legacy import import_legacy

    config = load_config()
    db = Database(config.db_path)
    report = import_legacy(db, args.directory)
    db.close()
    print(
        f"Importado en {config.db_path}: {report.open_forecasts} predicciones abiertas, "
        f"{report.resolved_forecasts} resueltas, {report.goals} objetivos, {report.settings} ajustes."
    )
    if report.missing:
        print(f"No encontrados (se omiten): {', '.join(report.missing)}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from oraqlo.api.app import create_app
    from oraqlo.config import load_config

    config = load_config()
    host = args.host or config.server.host
    port = args.port or config.server.port
    if host not in ("127.0.0.1", "localhost", "::1"):
        logging.getLogger("oraqlo").warning(
            "Escuchando en %s: la autenticación asume que solo llega tráfico local o de "
            "`tailscale serve`. No expongas este puerto a tu red.",
            host,
        )
    print(f"Oraqlo {__version__} en http://{host}:{port}  (datos: {config.home})")
    from oraqlo.updater import RESTART_EXIT_CODE

    app = create_app()
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="info", proxy_headers=False)
    )
    app.state.server = server  # el updater lo usa para pedir un reinicio limpio
    app.state.port = port  # para /api/server y para sincronizar tailscale serve
    server.run()
    if app.state.restart_requested:
        print("Reiniciando para activar la versión nueva…")
        return RESTART_EXIT_CODE
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    """Comprueba (y con --apply, prepara y activa) la última versión publicada."""
    from oraqlo.config import load_config
    from oraqlo.updater import UpdateError, Updater

    config = load_config()
    updater = Updater(config.home, config.updates.repo, config.updates.channel, config.db_path)
    try:
        result = updater.check()
        print(result.detail)
        if not args.apply or result.status != "available":
            return 0
        staged = updater.stage()
        print(staged.detail)
        if staged.status != "staged" or staged.version is None:
            return 1 if staged.status == "unmanaged" else 0
        backup = updater.activate(staged.version, config.backups_dir)
        print(f"Activada {staged.version} (backup de la base: {backup.name}). Reinicia Oraqlo.")
    except UpdateError as e:
        print(f"Actualización abortada: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_rollback(args: argparse.Namespace) -> int:
    from oraqlo.config import load_config
    from oraqlo.updater import UpdateError, Updater

    config = load_config()
    try:
        version = Updater(config.home, config.updates.repo).rollback()
    except UpdateError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Vuelta a {version}. Reinicia Oraqlo para aplicarlo.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oraqlo", description="Motor de estrategia bajo incertidumbre."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="logging detallado")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="muestra la versión instalada")

    p_serve = sub.add_parser("serve", help="arranca el servidor web (API + app)")
    p_serve.add_argument("--host", default=None, help="por defecto server.host (127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=None, help="por defecto server.port (8765)")
    p_update = sub.add_parser("update", help="busca (y con --apply instala) la última versión")
    p_update.add_argument("--apply", action="store_true", help="descargar, verificar y activar")
    sub.add_parser("rollback", help="vuelve a la versión instalada anterior")
    sub.add_parser("config", help="muestra rutas y roles efectivos")
    p_doctor = sub.add_parser("doctor", help="diagnóstico de configuración, base y proveedores")
    p_doctor.add_argument(
        "--preflight", action="store_true", help="sin llamadas de red (solo config, base y claves)"
    )
    p_import = sub.add_parser("import-legacy", help="importa los JSON de la versión TUI")
    p_import.add_argument("directory", nargs="?", default=".", help="carpeta con oraqlo_*.json")

    p_practice = sub.add_parser("practice", help="autopráctica con Polymarket", add_help=False)
    p_practice.add_argument("rest", nargs=argparse.REMAINDER)
    return parser


def _utf8_stdio() -> None:
    """La consola de Windows usa cp1252 por defecto: acentos ilegibles sin esto."""
    import io

    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "practice":
        return _cmd_practice(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "update":
        return _cmd_update(args)
    if args.command == "rollback":
        return _cmd_rollback(args)
    if args.command == "config":
        return _cmd_config(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "import-legacy":
        return _cmd_import_legacy(args)
    return 2  # inalcanzable: argparse exige subcomando


if __name__ == "__main__":
    raise SystemExit(main())
