# Oraqlo — Arquitectura

Motor de estrategia bajo incertidumbre, servido como app web local y accesible desde el celular por Tailscale.

## Vista general

```
Celular / PC (PWA React)                           este PC Windows
   │  HTTPS (identidad Tailscale)                ┌──────────────────────────────────────┐
   └──► tailscale serve :8443 ─► 127.0.0.1:<port> ─► │ FastAPI  (api/)                      │
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
  - `LLMForecaster`: escenarios con probabilidad, con una tasa base en cifras.
  - `committee.py`: el comité de modelos. Varias IA de familias distintas, cada una con un enfoque (visión externa, interna, abogado del diablo, ritmo en el tiempo), combinadas en log-odds con un peso por modelo según su habilidad en la Práctica (encogida hacia «todos iguales» con pocos casos). Lo que dice cada una se registra aparte (`llm:<modelo>:miembro`), fuera del boletín.
  - `markets.py`: busca en Polymarket, Manifold y Metaculus (con token) un mercado que pregunte exactamente lo mismo (lo confirma un modelo) y lo suma al comité como la voz `mercado:<plataforma>`. El peso del de Polymarket sale también de su historial en la Práctica.
  - `decompose.py`: estimación por partes (Fermi) de preguntas compuestas, «y» (cadena condicional) u «o». Entra como la voz `estructura`.
  - `numeric.py`: preguntas de cantidad como intervalos contiguos. Los límites viajan en un supuesto y se resuelve escribiendo el número.
  - `QuantForecaster`: Holt con intervalo que crece con √h; con umbral produce una binaria resoluble.
- **`memory/recalibration.py`**: un factor `a` por modelo (o para el comité) aprendido solo de previsiones válidas, encogido hacia 1 y activado desde 15 resueltas únicamente si mejora en validación cruzada. Además hay un factor propio por grupo (tipo · binaria/múltiple · plazo), encogido hacia el global y validado igual.
- **`memory/reference.py`**: clase de referencia propia (TF-IDF sobre las resueltas). Le cuenta a la visión externa qué pasó en preguntas parecidas.
- **`evaluate.py`** (`oraqlo evaluar`, `/api/evaluate`): compara sobre lo resuelto, en validación cruzada, las formas de predecir (recalibración, comité, mercado).
- **`simulator/`**:
  - `MonteCarloEngine` y `TreeSearchEngine` (expectimax).
  - `with_adversary()` compone un `AdversaryModel` (p. ej. `SoftmaxAdversary`) dentro de cualquier motor.
- **`strategy/planner.py`**: `ExpectedUtilityPlanner`, utilidad esperada + CVaR que descarta acciones con riesgo de ruina.
- **`strategy/sensitivity.py`**: puntos de ruptura (probabilidades y utilidades) y valor de la información por acción, con el mismo planificador.
- **`casefile.py`**: el caso puede traer un `rival` que reacciona a cada jugada según su utilidad (`SoftmaxAdversary` + `with_adversary`).
- **`panel.py`**: debate secuencial de 4 roles con transcript completo. El Red Team hace además un pre-mortem con señales tempranas.
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
- **`Scheduler`**: tareas periódicas cuya última ejecución se guarda en la base. Son la práctica, la vigilancia, los avisos de hitos, las actualizaciones y el backup diario.
- **`watch.py`** (vigilancia): señales por previsión u objetivo guardadas en `settings` (`watch.<id>`). La tarea `vigilar` (un job) investiga lo nuevo con presupuesto diario y, si importa, vuelve a predecir encadenando versiones (o hace un check-in del objetivo). Avisa si la probabilidad se mueve lo bastante o salta una señal. Al resolver una versión se resuelven todas.
- **`PushService`**: Web Push con VAPID, que limpia solo las suscripciones muertas.
- **`KeepAwake`**: evita que Windows se suspenda mientras haya análisis en curso.

### API y frontend

- **`api/app.py`** (FastAPI):
  - REST + SSE.
  - Sirve la app compilada (SPA) desde `api/static/`, que viaja dentro del wheel.
  - Autenticación en **`api/auth.py`**: acepta solo localhost sin proxy o la identidad `Tailscale-User-Login` (con allowlist opcional). Rechaza el tráfico de Funnel ([ADR 0002](decisions/0002-web-tailscale.md)).
- **`web/`**: React 19 + TypeScript + Tailwind + TanStack Query, mobile-first. Es una PWA con un service worker propio para la precaché y las notificaciones push.

### Despliegue y actualización

- **`installer/oraqlo.iss`** → **`Oraqlo-Setup.exe`** (Inno Setup, compilado en CI): instalación por usuario sin administrador. Empaqueta el wheel, `uv.exe` (con versión y hash fijados) y el script de instalación. Lo que hace es preguntar y mostrar el progreso; el trabajo lo delega en el script. También registra la desinstalación en "Aplicaciones instaladas".
- **`scripts/install-windows.ps1`**: el único camino de instalación, tanto para el .exe como a mano. Instala la release en `versions/<X.Y.Z>` y registra la tarea programada. Con `-Uv`, descarga un Python propio en `app/python`, así que el PC no necesita Python. También crea el acceso directo del menú Inicio y, si Tailscale está listo, ejecuta `tailscale serve`.
- **`launcher.py`**: solo stdlib, se copia fuera de las versiones. Arranca el servidor y reinicia con el código 75. Si la versión pendiente no supera `/api/health` en 60 s, hace **rollback**. Con `--open`, que es lo que usa el acceso directo, abre el navegador y arranca la tarea si hacía falta.
- **Tailscale** (`services/network.py`): Oraqlo se publica en su propio puerto HTTPS de la tailnet (`:8443`, o el siguiente libre) y nunca en la raíz, que queda para otras apps. Al arrancar, `migrate_from_root` retira solo su propio handler de la raíz si una versión ≤ 0.5 la ocupó. `tailscale.txt` guarda el puerto publicado.
- **Prioridad**: la tarea programada arranca todo por debajo de lo normal (CPU y disco). El lanzador crea el servidor en prioridad normal y `oraqlo serve` se sube a normal al arrancar (`SetPriorityClass` con tipos de ctypes explícitos), para que el servidor y el `pip` de las actualizaciones no se arrastren con el PC ocupado.
- **Lanzador actualizable**: al arrancar, el servidor copia su `oraqlo/launcher.py` a `app/launcher.py` si cambió (`updater.refresh_launcher`; valida que compile y deja `launcher.prev.py`). Así las mejoras del lanzador llegan con el auto-update; el proceso que corre conserva su código hasta el siguiente arranque.
- **App de escritorio** (`desktop.py`, Windows):
  - Es una ventana **WebView2** (`pywebview`) que muestra la misma app web servida en 127.0.0.1, más un icono en la bandeja (`pystray`).
  - Cerrar la ventana la esconde. Hay una sola instancia: una segunda apertura deja la ruta en `app/open-path.txt` y la ventana viva la atiende.
  - Si no hay WebView2 o algo falla, sale con el código 3 y `launcher.py --open` abre el navegador.
  - Los avisos nativos (`services/notify.py`, `windows-toasts`) los emite el servidor (`PushService.send`), así que llegan aunque la ventana esté cerrada. Al pulsarlos se ejecuta `launcher.py --open --path`.
- **Configuración desde la app** (`services/userconfig.py`, `services/doctor.py`):
  - La bienvenida y Ajustes escriben `.env` (claves, de forma atómica) y `oraqlo.toml` (con tomlkit, que conserva los comentarios). Antes de guardar se valida con el cargador real.
  - Los cambios se recargan en caliente: `OraqloService.reload_config` actualiza el mismo objeto de config y vacía la caché de proveedores.
  - Estas rutas exigen acceso local (`require_local`): desde la tailnet se pueden leer, pero no cambiar.
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
- **Puntuar previsiones de intervalo continuas (CRPS):** las preguntas de cantidad se reparten en intervalos y se puntúan con Brier. El `QuantForecaster` con umbral da una binaria resoluble.
