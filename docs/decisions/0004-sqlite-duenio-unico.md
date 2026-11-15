# ADR 0004 — SQLite como dueño único del estado

**Estado:** aceptado · **Fecha:** 2026-11-15

## Contexto

El estado vivía en `oraqlo_ledger.json`, `oraqlo_goals.json` y `oraqlo_settings.json`,
relativos al directorio de trabajo, reescritos completos en cada cambio, sin lock ni
escritura atómica. TUI y CLI se pisaban; un JSON corrupto impedía arrancar.

## Decisión

- Una base **SQLite** (WAL) en el directorio de datos del usuario, propiedad del
  servidor. Escritura fila a fila en transacciones.
- Migraciones versionadas con `PRAGMA user_version` (sin Alembic: un archivo y un
  proceso no lo justifican). Una base de una versión más nueva se rechaza con mensaje claro.
- Backups en caliente: diario (7 días) y antes de cada actualización.
- `oraqlo import-legacy` migra los JSON sin re-puntuar: el historial de calibración se
  conserva tal cual.

## Consecuencias

- (+) Sin pérdidas por escrituras concurrentes; consultas de calibración en SQL.
- (+) La actualización prueba las migraciones sobre una copia antes de tocar la base real.
- (−) Los datos ya no son JSON legibles a mano (mitigado: informes Markdown y API).
