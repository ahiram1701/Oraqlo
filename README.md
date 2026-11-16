# Oraqlo

**Motor de estrategia bajo incertidumbre.** Le haces una pregunta o le das un objetivo y Oraqlo:

- **prevé** con probabilidades calibradas, nunca con certezas;
- **simula** futuros y oponentes;
- somete el plan a un **debate adversarial**: Estratega ▸ Red Team ▸ Oráculo ▸ Juez;
- **recomienda** la jugada de mayor utilidad esperada ajustada a riesgo.

Después **aprende**. Cada predicción se puntúa contra lo que realmente pasó (Brier score) y esa calibración pondera las siguientes.

Funciona como una **app web instalable en el celular** que corre en tu PC y llega a cualquier dispositivo tuyo por **Tailscale**.

## Lo que Oraqlo NO es

No "predice todo" ni "controla la realidad": la realidad es caótica, parcialmente observable y adversarial. La fuerza de Oraqlo es **cuantificar lo que no sabe**. Toda previsión es una distribución, se mide contra el resultado real y se recalibra. Ver [ADR 0001](docs/decisions/0001-oraculo-honesto.md).

## Qué puedes hacer

| Pantalla | Para qué |
|---|---|
| **Preguntar** | Previsión rápida (1 llamada), análisis completo (el LLM redacta acciones y escenarios, simula y debate) o **lograr objetivo** (plan, palancas, hitos y *uplift* medible). Con investigación web opcional. |
| **Objetivos** | Seguimiento: check-ins en lenguaje natural, trayectoria de P(objetivo), hitos con aviso de vencidos, re-planificación y cierre. |
| **Ledger** | Predicciones abiertas: las resuelves con un toque y alimentan la calibración. |
| **Práctica** | Auto-mejora con Polymarket, solo lectura. Oraqlo predice sin ver el precio y compara su Brier con el de la multitud. Corre sola en el servidor. |
| **Calibración** | Diagrama de fiabilidad y Brier por fuente/modelo. |
| **Caso · Historial · Ajustes** | Editor de casos, informes en Markdown, roles, proveedores, notificaciones y actualizaciones. |

Los análisis largos corren en el servidor: **puedes bloquear el celular** y al volver el resultado está ahí. Si activas las notificaciones push, te avisa al terminar.

## Instalación (Windows)

Requisitos: **Python 3.12+** y, para el celular, **[Tailscale](https://tailscale.com/download)** en el PC y en el teléfono.

Abre una PowerShell normal (Win+X → Terminal; no la terminal integrada de otra app) y ejecuta:

```powershell
irm https://github.com/ahiram1701/Oraqlo/releases/latest/download/install-windows.ps1 -OutFile $env:TEMP\install-oraqlo.ps1
powershell -ExecutionPolicy Bypass -File $env:TEMP\install-oraqlo.ps1
```

No tienes que elegir puerto. Oraqlo usa el 8765 y, si otra aplicación lo ocupa, toma el siguiente libre. Lo hace al instalar y también cada vez que arranca. La dirección del celular no cambia, porque Oraqlo mantiene `tailscale serve` apuntando al puerto real. Si prefieres otro puerto, cámbialo en **Ajustes → Conexión**.

El instalador hace lo siguiente:

1. Descarga la última release y verifica su SHA256.
2. La instala en `%LOCALAPPDATA%\Oraqlo`.
3. Registra la tarea programada **Oraqlo**, que arranca al iniciar sesión.
4. Publica la app en tu tailnet con `tailscale serve`.
5. Al terminar, te muestra la URL `https://<tu-pc>.<tu-tailnet>.ts.net`.

Ábrela en el celular y elige **"Añadir a pantalla de inicio"**.

- ¿Vienes de la versión TUI? Añade `-ImportFrom C:\ruta\con\oraqlo_ledger.json` para conservar tu historial de calibración y tus objetivos.
- `-Uninstall` quita la tarea y la publicación. Tus datos se conservan.

### Actualizaciones automáticas

Oraqlo comprueba las releases cada 6 h. Cuando hay una versión nueva, la instala él solo:

1. Verifica el hash y la procedencia del paquete.
2. Prueba la versión nueva sobre una copia de tu base de datos.
3. Hace un backup de la base real.
4. Espera a que no haya ningún análisis en curso y la activa.

Si la versión nueva no arranca, **vuelve sola a la anterior** y te avisa.

Configúralo con `[updates] mode = "auto" | "notify" | "off"`. A mano: `oraqlo update --apply` y `oraqlo rollback`.

## Configuración

`oraqlo config` te dice dónde está todo. Copia [config/oraqlo.example.toml](config/oraqlo.example.toml) a esa ruta como `oraqlo.toml`.

Cada rol del panel (estratega, red_team, oraculo, juez, investigador, redactor, practica) puede usar **un proveedor y un modelo distinto**:

| Proveedor | Clave (variable de entorno) |
|---|---|
| `anthropic` (Claude; por defecto `claude-opus-5-5`, con `effort` por rol) | `ANTHROPIC_API_KEY` |
| `ollama-local` / `ollama-cloud` | — / `OLLAMA_API_KEY` |
| `openai`, `gemini`, `openrouter`, `groq`, `deepseek`, `mistral`, `xai` | `OPENAI_API_KEY`, `GEMINI_API_KEY`, … |
| `lmstudio` o cualquier API compatible con OpenAI (`[providers.x]`) | la que definas |

Sin configuración, Oraqlo elige el proveedor según las claves que encuentre: Anthropic, luego Ollama cloud, luego Ollama local.

Las claves **solo** se leen del entorno o del archivo `.env` del directorio de datos. Nunca van en el TOML ni salen por la API.

La investigación web usa la API de búsqueda de Ollama (`OLLAMA_API_KEY`). Sin esa clave, Oraqlo avisa y sigue sin investigar.

`oraqlo doctor` comprueba la configuración, la base de datos, las claves y que los proveedores respondan.

## Seguridad y privacidad

- El servidor escucha **solo en `127.0.0.1`**. Desde fuera solo se llega por `tailscale serve`, que autentica cada dispositivo de tu tailnet. Puedes restringir aún más con `server.allowed_users`.
- Se rechaza el tráfico que llega por un proxy sin identidad de Tailscale, incluido Tailscale Funnel (internet público).
- Tus datos (base SQLite, informes y backups diarios) viven en `%LOCALAPPDATA%\Oraqlo` y nunca entran al repositorio.

## Desarrollo

```bash
pip install uv
uv sync --all-extras                  # entorno Python
uv run oraqlo serve                   # API + app compilada en http://127.0.0.1:8765
cd web && npm install && npm run dev  # frontend con recarga en caliente (proxy a la API)
uv run pytest -q                      # tests Python (offline)
cd web && npm test                    # tests del frontend
```

`Oraqlo.bat` arranca el servidor desde el checkout y abre el navegador. Un checkout de desarrollo **nunca se auto-actualiza**.

La forma de trabajo (ramas, PRs, Conventional Commits, CI, releases y auto-merge) está en [CONTRIBUTING.md](CONTRIBUTING.md). La arquitectura está en [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Licencia

© 2026 Alberto H. Saucedo G. Proyecto **sin licencia de código abierto**: todos los derechos reservados. Contacta al autor para permisos de uso.
