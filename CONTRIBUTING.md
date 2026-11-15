# Forma de trabajo

## Entorno

```bash
pip install uv                 # una vez
uv sync --all-extras           # crea .venv con todo (dev incluido)
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg
```

Comandos habituales:

```bash
uv run pytest -q               # tests (100% offline)
uv run ruff check --fix .      # lint
uv run ruff format .           # formato
uv run pyright                 # tipos
uv run oraqlo --help           # CLI
```

## Flujo: rama → PR → CI verde → squash-merge

1. `main` está protegida: nada se sube directo.
2. Cada cambio va en una rama (`feat/...`, `fix/...`, `chore/...`) y entra por PR.
3. El PR solo se mergea con el **CI en verde**: lint + tipos + tests en Ubuntu y
   **Windows** (la plataforma donde corre Oraqlo) con Python 3.12 y 3.13, y build del wheel.
4. Se mergea con **squash**: el título del PR se convierte en el commit de `main`.

## Commits y versiones (Conventional Commits → SemVer)

El título del PR debe seguir [Conventional Commits](https://www.conventionalcommits.org/);
un check lo valida. Ese prefijo decide la siguiente versión:

| Prefijo | Ejemplo | Efecto en la versión |
|---|---|---|
| `fix:` | `fix: el ledger no normaliza mayúsculas` | parche (0.2.0 → 0.2.1) |
| `feat:` | `feat: proveedor Anthropic` | menor (0.2.0 → 0.3.0) |
| `feat!:` / `BREAKING CHANGE:` | `feat!: nuevo formato de config` | mayor (antes de 1.0: menor) |
| `docs:`, `refactor:`, `test:`, `ci:`, `chore:`, `build:` | | sin release propia |

## Releases (CD)

Automático con [release-please](https://github.com/googleapis/release-please):

1. Cada merge a `main` actualiza un **PR de release** (CHANGELOG + próxima versión).
2. Cuando quieras publicar, **mergea ese PR**: se crea el tag `vX.Y.Z` y la GitHub Release.
3. El workflow construye el wheel (con el frontend compilado dentro, desde la Fase 3),
   genera `SHA256SUMS` y una attestation de procedencia, y los adjunta a la Release.
4. El Oraqlo instalado en el PC detecta la Release y se actualiza solo (Fase 4b).

La versión no se escribe a mano en ningún archivo: se deriva del tag git (`hatch-vcs`).

## Dependencias

Dependabot abre PRs semanales para dependencias de Python y GitHub Actions.
Para añadir una: `uv add <paquete>` (o `uv add --dev <paquete>`), y commitea `uv.lock`.
