"""Punto de entrada único: el comando `oraqlo`.

    oraqlo version                 muestra la versión instalada
    oraqlo tui [caso.json]         abre la TUI (requiere el extra `tui`)
    oraqlo practice [opciones]     autopráctica con Polymarket (ver --help)

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


def _cmd_tui(args: argparse.Namespace) -> int:
    try:
        from oraqlo.tui.app import OraqloTUI
    except ModuleNotFoundError as e:
        if "textual" in str(e):
            print("La TUI necesita Textual: pip install 'oraqlo[tui]'", file=sys.stderr)
            return 1
        raise
    OraqloTUI(case_path=args.case).run()
    return 0


def _cmd_practice(args: argparse.Namespace) -> int:
    from oraqlo.autopractice import main as practice_main

    return practice_main(args.rest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oraqlo", description="Motor de estrategia bajo incertidumbre."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="logging detallado")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="muestra la versión instalada")

    p_tui = sub.add_parser("tui", help="abre la interfaz de terminal")
    p_tui.add_argument("case", nargs="?", default="cases/mi_caso.json")

    p_practice = sub.add_parser("practice", help="autopráctica con Polymarket", add_help=False)
    p_practice.add_argument("rest", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "tui":
        return _cmd_tui(args)
    if args.command == "practice":
        return _cmd_practice(args)
    return 2  # inalcanzable: argparse exige subcomando


if __name__ == "__main__":
    raise SystemExit(main())
