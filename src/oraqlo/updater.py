"""Auto-actualización desde GitHub Releases (modelo *pull*, con rollback).

Estructura de una instalación gestionada (la crea scripts/install-windows.ps1):

    <home>/app/
        launcher.py          lanzador estable (solo stdlib): arranca, vigila y revierte
        current.txt          versión activa ("0.4.0")
        previous.txt         versión anterior (destino del rollback)
        pending.txt          versión recién aplicada aún sin health check superado
        versions/<X.Y.Z>/    un venv por versión
        update.log           historial de actualizaciones y rollbacks
    <home>/data/oraqlo.db    la base vive FUERA de las versiones
    <home>/backups/          copias antes de cada actualización y diarias

Flujo: check (API pública de GitHub, sin token) → descarga del wheel +
SHA256SUMS → verificación del hash (y attestation si `gh` está disponible) →
venv nuevo → prueba de arranque y migración sobre una COPIA de la base →
backup de la base real → current.txt = nueva → el servidor sale con código 75 y
el lanzador arranca la versión nueva. Si no responde, el lanzador revierte.

Solo una instalación gestionada se actualiza sola: un checkout de desarrollo
nunca se sobrescribe.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from packaging.version import InvalidVersion, Version

from oraqlo import __version__

log = logging.getLogger(__name__)

RESTART_EXIT_CODE = 75  # EX_TEMPFAIL: "reiníciame, hay versión nueva"
API = "https://api.github.com"
USER_AGENT = "oraqlo-updater"


class UpdateError(RuntimeError):
    """La actualización no pudo completarse (la versión actual sigue intacta)."""


class NoReleases(UpdateError):
    """El repositorio aún no tiene releases publicadas (404 de /releases/latest)."""


@dataclass
class ReleaseInfo:
    tag: str
    version: str
    wheel_url: str
    wheel_name: str
    sums_url: str | None
    html_url: str
    prerelease: bool


# ── estructura de la instalación ──────────────────────────────────────────


class Layout:
    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.root = self.home / "app"
        self.versions = self.root / "versions"

    def _read(self, name: str) -> str | None:
        path = self.root / name
        if not path.exists():
            return None
        # utf-8-sig: tolera el BOM que añade PowerShell 5.1.
        return path.read_text(encoding="utf-8-sig").strip() or None

    def _write(self, name: str, value: str | None) -> None:
        path = self.root / name
        if value is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")

    @property
    def current(self) -> str | None:
        return self._read("current.txt")

    @property
    def previous(self) -> str | None:
        return self._read("previous.txt")

    @property
    def pending(self) -> str | None:
        return self._read("pending.txt")

    def venv(self, version: str) -> Path:
        return self.versions / version

    def python(self, version: str) -> Path:
        scripts = "Scripts" if os.name == "nt" else "bin"
        exe = "python.exe" if os.name == "nt" else "python"
        return self.venv(version) / scripts / exe

    def switch_to(self, version: str) -> None:
        """Activa `version`: la actual pasa a ser la anterior y queda pendiente de validar."""
        current = self.current
        if current and current != version:
            self._write("previous.txt", current)
        self._write("current.txt", version)
        self._write("pending.txt", version)

    def log(self, message: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        with (self.root / "update.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {message}\n")

    def installed_versions(self) -> list[str]:
        if not self.versions.exists():
            return []
        return sorted((p.name for p in self.versions.iterdir() if p.is_dir()), key=_vkey)

    def prune(self) -> list[str]:
        """Borra las versiones viejas: se conservan solo la actual y la anterior (rollback)."""
        protected = {self.current, self.previous}
        removed = [v for v in self.installed_versions() if v not in protected]
        for version in removed:
            shutil.rmtree(self.venv(version), ignore_errors=True)
        return removed


def _vkey(v: str) -> Version:
    try:
        return Version(v)
    except InvalidVersion:
        return Version("0")


def is_managed(layout: Layout) -> bool:
    """¿Este proceso corre desde una versión instalada por el instalador?"""
    try:
        Path(sys.prefix).resolve().relative_to(layout.versions.resolve())
        return True
    except ValueError:
        return False


def is_newer(candidate: str, current: str | None = None) -> bool:
    # `current` se resuelve al llamar (no al definir la función): __version__ del módulo.
    try:
        return Version(candidate) > Version(current or __version__)
    except InvalidVersion:
        return False


# ── GitHub ────────────────────────────────────────────────────────────────


def _get_json(url: str, timeout: float = 15.0) -> object:
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
            raise UpdateError(
                "Límite de la API de GitHub alcanzado; se reintentará más tarde."
            ) from e
        if e.code == 404:
            raise NoReleases("El repositorio aún no tiene releases publicadas.") from e
        raise UpdateError(f"GitHub respondió {e.code} al consultar releases.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"Sin conexión con GitHub: {e}") from e


def _release_from(data: dict) -> ReleaseInfo | None:
    tag = str(data.get("tag_name", ""))
    # Tolera "v1.2.3" y "oraqlo-v1.2.3" (etiquetas con componente de release-please).
    match = re.search(r"v?(\d+\.\d+\.\d+\S*)$", tag)
    if not match:
        return None
    version = match.group(1)
    assets = {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}
    wheel = next((n for n in assets if n.endswith(".whl")), None)
    if not wheel or data.get("draft"):
        return None
    return ReleaseInfo(
        tag=tag,
        version=version,
        wheel_url=assets[wheel],
        wheel_name=wheel,
        sums_url=assets.get("SHA256SUMS"),
        html_url=str(data.get("html_url", "")),
        prerelease=bool(data.get("prerelease")),
    )


def latest_release(repo: str, channel: str = "stable", fetch=_get_json) -> ReleaseInfo | None:
    """Última release con wheel adjunto. `beta` incluye pre-releases."""
    if channel == "stable":
        try:
            data = fetch(f"{API}/repos/{repo}/releases/latest")
        except NoReleases:
            return None
        return _release_from(data) if isinstance(data, dict) else None
    data = fetch(f"{API}/repos/{repo}/releases?per_page=10")
    releases = (
        [r for r in (_release_from(d) for d in data if isinstance(d, dict)) if r]
        if isinstance(data, list)
        else []
    )
    return max(releases, key=lambda r: _vkey(r.version), default=None)


def _download(url: str, dest: Path, timeout: float = 120.0) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"No se pudo descargar {url}: {e}") from e
    return dest


def verify_sha256(path: Path, sums_text: str) -> None:
    expected = None
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == path.name:
            expected = parts[0].lower()
    if expected is None:
        raise UpdateError(f"SHA256SUMS no incluye {path.name}: actualización abortada.")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise UpdateError(
            f"Hash de {path.name} no coincide: actualización abortada (posible manipulación)."
        )


def verify_attestation(path: Path, repo: str) -> bool | None:
    """Verifica la procedencia con `gh attestation verify` si `gh` está disponible.

    Devuelve True/False, o None si no se pudo comprobar (sin `gh` o sin sesión).
    """
    gh = shutil.which("gh")
    if not gh:
        return None
    result = subprocess.run(
        [gh, "attestation", "verify", str(path), "--repo", repo],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        return True
    if "authentication" in (result.stderr or "").lower() or "gh auth login" in (
        result.stderr or ""
    ):
        return None
    return False


# ── lanzador ──────────────────────────────────────────────────────────────


def refresh_launcher(layout: Layout, source: Path | None = None) -> bool:
    """Copia a app/launcher.py el lanzador de la versión que está corriendo.

    El instalador copia el lanzador una vez y el auto-update solo cambia de
    versión, así que sin esto las mejoras del lanzador (p. ej. la ventana nativa)
    nunca llegarían. Se llama al ARRANCAR el servidor: así lo hace el código de la
    versión nueva, no el de la vieja que aplicó la actualización. True si cambió.
    """
    import py_compile
    import tempfile

    target = layout.root / "launcher.py"
    if not target.exists():
        return False  # no es una instalación gestionada
    if source is None:
        from oraqlo import launcher

        source = Path(launcher.__file__)
    new = source.read_bytes()
    if new == target.read_bytes():
        return False
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "launcher.py"
        probe.write_bytes(new)
        try:
            py_compile.compile(str(probe), cfile=str(Path(tmp) / "launcher.pyc"), doraise=True)
        except py_compile.PyCompileError as e:
            layout.log(f"lanzador nuevo inválido, se conserva el actual: {e.msg}")
            return False
    shutil.copy2(target, layout.root / "launcher.prev.py")
    staging = layout.root / "launcher.py.new"
    staging.write_bytes(new)
    os.replace(staging, target)
    layout.log("lanzador actualizado desde la versión activa")
    return True


# ── instalación de una versión ────────────────────────────────────────────


def _base_python() -> str:
    return getattr(sys, "_base_executable", None) or sys.executable


def install_version(layout: Layout, version: str, wheel: Path, python: str | None = None) -> Path:
    """Crea versions/<version> (venv) e instala el wheel con sus dependencias."""
    venv = layout.venv(version)
    if venv.exists():
        shutil.rmtree(venv)
    run = lambda *cmd: subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=900)
    try:
        run(python or _base_python(), "-m", "venv", str(venv))
        run(
            str(layout.python(version)),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-q",
            str(wheel),
        )
    except subprocess.CalledProcessError as e:
        shutil.rmtree(venv, ignore_errors=True)
        raise UpdateError(f"Fallo instalando {version}: {(e.stderr or '')[-800:]}") from e
    return venv


PREFLIGHT = (
    "import sys; from oraqlo.store.db import Database; import oraqlo.api.app; "
    "db = Database(sys.argv[1]); print(db.version); db.close()"
)


def preflight(layout: Layout, version: str, db_path: Path) -> None:
    """Arranca la versión nueva contra una COPIA de la base (migraciones incluidas)."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "preflight.db"
        if db_path.exists():
            from oraqlo.store.db import Database

            src = Database(db_path)
            src.backup_to(copy)
            src.close()
        result = subprocess.run(
            [str(layout.python(version)), "-c", PREFLIGHT, str(copy)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise UpdateError(
                f"La versión {version} no pasó la prueba de arranque: {result.stderr[-800:]}"
            )


# ── orquestación ──────────────────────────────────────────────────────────


@dataclass
class UpdateResult:
    status: str  # "up-to-date" | "available" | "staged" | "unmanaged" | "error"
    detail: str
    version: str | None = None


class Updater:
    def __init__(
        self, home: Path, repo: str, channel: str = "stable", db_path: Path | None = None
    ) -> None:
        self.layout = Layout(home)
        self.repo = repo
        self.channel = channel
        self.db_path = db_path or (Path(home) / "data" / "oraqlo.db")
        self.fetch = _get_json
        self.download = _download

    def check(self) -> UpdateResult:
        release = latest_release(self.repo, self.channel, fetch=self.fetch)
        if release is None:
            return UpdateResult("up-to-date", "No hay releases publicadas todavía.")
        if not is_newer(release.version):
            return UpdateResult(
                "up-to-date", f"Estás en la última versión ({__version__}).", release.version
            )
        return UpdateResult(
            "available", f"Nueva versión disponible: {release.version}.", release.version
        )

    def stage(self, managed: bool | None = None) -> UpdateResult:
        """Descarga, verifica, instala y prueba la versión nueva; NO la activa."""
        managed = is_managed(self.layout) if managed is None else managed
        release = latest_release(self.repo, self.channel, fetch=self.fetch)
        if release is None or not is_newer(release.version):
            return UpdateResult("up-to-date", f"Estás en la última versión ({__version__}).")
        if not managed:
            return UpdateResult(
                "unmanaged",
                f"Hay versión {release.version}, pero esta copia no es una instalación gestionada "
                f"(¿checkout de desarrollo?): actualízala con git.",
                release.version,
            )
        with tempfile.TemporaryDirectory() as tmp:
            wheel = self.download(release.wheel_url, Path(tmp) / release.wheel_name)
            if not release.sums_url:
                raise UpdateError("La release no trae SHA256SUMS: actualización abortada.")
            sums = self.download(release.sums_url, Path(tmp) / "SHA256SUMS").read_text(
                encoding="utf-8"
            )
            verify_sha256(wheel, sums)
            attested = verify_attestation(wheel, self.repo)
            if attested is False:
                raise UpdateError(
                    "La attestation de procedencia no es válida: actualización abortada."
                )
            install_version(self.layout, release.version, wheel)
        preflight(self.layout, release.version, self.db_path)
        self.layout.log(
            f"preparada {release.version} (hash ok, attestation {'ok' if attested else 'no comprobada'})"
        )
        return UpdateResult(
            "staged", f"Versión {release.version} lista para activarse.", release.version
        )

    def activate(self, version: str, backup_dir: Path) -> Path:
        """Backup de la base y cambio de puntero. Tras esto, salir con RESTART_EXIT_CODE."""
        backup = backup_dir / f"pre-{version}-{datetime.now(UTC):%Y%m%d-%H%M%S}.db"
        if self.db_path.exists():
            from oraqlo.store.db import Database

            db = Database(self.db_path)
            db.backup_to(backup)
            db.close()
        self.layout.switch_to(version)
        self.layout.log(f"activada {version} (backup: {backup.name})")
        return backup

    def rollback(self) -> str:
        previous = self.layout.previous
        if not previous or not self.layout.venv(previous).exists():
            raise UpdateError("No hay versión anterior instalada a la que volver.")
        current = self.layout.current
        self.layout._write("current.txt", previous)
        self.layout._write("previous.txt", current)
        self.layout._write("pending.txt", None)
        self.layout.log(f"rollback manual {current} → {previous}")
        return previous
