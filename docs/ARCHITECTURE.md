# Oraqlo — Arquitectura

Motor de estrategia bajo incertidumbre, servido como app web local y accesible desde el celular por Tailscale.

## Vista general

```
Celular / PC (PWA React)                           este PC Windows
   │  HTTPS (identidad Tailscale)                ┌──────────────────────────────────────┐
   └──► tailscale serve ──► 127.0.0.1:<port> ──► │ FastAPI  (api/)                      │
                                                 │   auth Tailscale · REST · SSE        │
                                                 │ Servicios (services/)                │
                                                 │   OraqloService · JobRunner ·        │
                                                 │   Scheduler · Push · KeepAwake       │
                                                 │ Dominio                              │
                                                 │   agent (OODA) · forecaster ·        │
                                                 │   simulator · planner · panel ·      │
                                                 │   goals · research · autopractice    │
                                                 │ LLM (llm/)  anthropic · openai-compat│
                                                 │             · ollama                 │
                                                 │ SQLite (store/)  único dueño estado  │
                                                 └──────────────────────────────────────┘
        tarea programada ──► launcher.py ──► versions/<X.Y.Z>/ (updater + rollback)
```

## Principios de diseño

1. **Calibración > confianza.** Toda predicción es una distribución, se puntúa con Brier y se recalibra ([ADR 0001](decisions/0001-oraculo-honesto.md)).
2. **Auto-crítica adversarial.** Ninguna estrategia sale sin pasar por el Red Team.
3. **Modularidad enchufable.** Proveedores LLM, forecasters, motores de simulación y conectores son piezas detrás de contratos.
4. **Trazabilidad.** Cada análisis guarda su previsión, su debate completo y su veredicto (tabla `runs`, informe Markdown).
5. **Un solo dueño del estado.** El servidor y su base SQLite ([ADR 0004](decisions/0004-sqlite-duenio-unico.md)).

## Capas

### Dominio (sin I/O de interfaz)

- **`world_model.py`**: estados versionados e inmutables (`apply`, `at`) con estatus epistémico por variable.
- **`connectors/`**: `StaticConnector` y `FileConnector` (CSV/JSON/JSONL incremental), que alimentan el world model.
- **`forecaster/`**:
  - `LLMForecaster`: escenarios con probabilidad.
  - `QuantForecaster`: Holt con intervalo que crece con √h; con umbral produce una binaria resoluble.
  - `EnsembleForecaster`: pondera cada miembro por 1/Brier y registra sus predicciones para que cada fuente aprenda.
- **`simulator/`**:
  - `MonteCarloEngine` y `TreeSearchEngine` (expectimax).
  - `with_adversary()` compone un `AdversaryModel` (p. ej. `SoftmaxAdversary`) dentro de cualquier motor.
- **`strategy/planner.py`**: `ExpectedUtilityPlanner`, utilidad esperada + CVaR que descarta acciones con riesgo de ruina.
- **`panel.py`**: debate secuencial de 4 roles con transcript completo.
- **`agent.py`**: `OraqloAgent.cycle()`, el ciclo OODA. Redelibera ante "revisar", pasa a la siguiente candidata ante "descartar" y emite eventos de progreso.
- **`goals.py` / `goaltrack.py`**: backcasting hacia un objetivo; seguimiento con hitos, check-ins y re-plan.
- **`research.py`**: investigación web iterativa (consultas → reflexión → lectura a fondo → síntesis).
- **`autopractice.py` / `polymarket.py`**: práctica de solo lectura contra mercados de predicción.
- **`memory/calibration.py`**: Brier multiclase, log-loss, `CalibrationLedger`. Las etiquetas se normalizan al resolver.

### LLM (`llm/`)

`LLMProvider.chat(messages, model, temperature, json_mode, **kwargs)` es la única interfaz. Los adaptadores son:

- `AnthropicProvider`: SDK oficial, `effort` por rol (Claude no admite `temperature`), salida estructurada nativa, *fallbacks* ante rechazos.
- `OpenAICompatProvider`: presets OpenAI, Gemini, OpenRouter, Groq, DeepSeek, Mistral, xAI y LM Studio, más proveedores propios.
- `OllamaProvider`: local y cloud, `format` = JSON Schema, reintentos con backoff.

`structured.py` es el único camino para pedir JSON: esquema nativo si el proveedor lo soporta, extracción robusta, validación Pydantic y reintento con el error como feedback. `registry.py` construye y cachea un proveedor por rol a partir de `oraqlo.toml` ([ADR 0003](decisions/0003-multi-proveedor-salida-estructurada.md)).

### Persistencia (`store/`)

SQLite en modo WAL, con migraciones versionadas por `PRAGMA user_version` y backup en caliente. Tablas:

- `forecasts` (ledger), `goals`, `settings`, `cases`, `runs`;
- `jobs` y `job_events`;
- `push_subscriptions`.

`SqliteLedger` y `SqliteGoalStore` heredan la lógica de dominio y solo cambian dónde se persiste. `legacy.py` importa los JSON de la versión TUI sin re-puntuar nada.

### Servicios (`services/`)

- **`OraqloService`**: todos los flujos sin interfaz. Previsión, análisis completo, ciclo sobre un caso, objetivo, check-in, re-plan, práctica, calibración.
- **`JobRunner`**: cada flujo largo es un job persistido. Sus eventos llevan número de secuencia, así que el SSE se reanuda con `Last-Event-ID`. Los jobs interrumpidos por un reinicio se marcan como fallidos.
- **`Scheduler`**: tareas periódicas cuya última ejecución se guarda en la base. Son la práctica, los avisos de hitos, las actualizaciones y el backup diario.
- **`PushService`**: Web Push con VAPID, que limpia solo las suscripciones muertas.
- **`KeepAwake`**: evita que Windows se suspenda mientras haya análisis en curso.

### API y frontend

- **`api/app.py`** (FastAPI):
  - REST + SSE.
  - Sirve la app compilada (SPA) desde `api/static/`, que viaja dentro del wheel.
  - Autenticación en **`api/auth.py`**: acepta solo localhost sin proxy o la identidad `Tailscale-User-Login` (con allowlist opcional). Rechaza el tráfico de Funnel ([ADR 0002](decisions/0002-web-tailscale.md)).
- **`web/`**: React 19 + TypeScript + Tailwind + TanStack Query, mobile-first. Es una PWA con un service worker propio para la precaché y las notificaciones push.

### Despliegue y actualización

- **`scripts/install-windows.ps1`**: instala la release en `versions/<X.Y.Z>`, registra la tarea programada y ejecuta `tailscale serve`.
- **`launcher.py`**: solo stdlib, se copia fuera de las versiones. Arranca el servidor y reinicia con el código 75. Si la versión pendiente no supera `/api/health` en 60 s, hace **rollback**.
- **`updater.py`**: modelo *pull* contra la API pública de GitHub. Su secuencia es:
  1. Verifica el SHA256 y la attestation.
  2. Crea un venv nuevo.
  3. Hace un preflight sobre una copia de la base.
  4. Hace un backup de la base real.
  5. Cambia el puntero de versión.

  Solo actúa en instalaciones gestionadas.

### CI/CD

- **CI**: en cada PR corren lint, tipos y tests en Ubuntu y Windows (py3.12/3.13), el frontend y el build del wheel con la app dentro.
- **CD**: release-please convierte los Conventional Commits en un PR de release. Al mergearlo se crean el tag y la Release, con el wheel, `SHA256SUMS` y la attestation adjuntos.

Ver [CONTRIBUTING.md](../CONTRIBUTING.md).

## Fuera de alcance

- **Actuar en el mundo real:** Oraqlo recomienda y prevé; un humano decide.
- **MCTS con presupuesto:** queda pendiente para árboles grandes. Hoy hay expectimax exacto y Monte Carlo.
- **Puntuar previsiones de intervalo:** para que se puedan resolver, el `QuantForecaster` debe usarse con umbral, que da una binaria resoluble.
