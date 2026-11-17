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
| **Preguntar** | Previsión rápida (1 llamada), análisis completo (el LLM redacta acciones y escenarios, simula y debate) o **lograr objetivo** (plan, palancas, hitos y *uplift* medible). Con investigación web opcional; si internet no basta, te pregunta lo que solo tú sabes (o sigue sin preguntar si lo prefieres). |
| **Objetivos** | Seguimiento: check-ins en lenguaje natural, trayectoria de P(objetivo), hitos con aviso de vencidos, re-planificación y cierre. |
| **Predicciones** | Lo que Oraqlo previó y aún no sabes cómo acabó: lo marcas con un toque y así aprende (calibración). |
| **Práctica** | Auto-mejora con Polymarket, solo lectura. Oraqlo predice sin ver el precio y compara su Brier con el de la multitud. Corre sola en el servidor. |
| **Calibración** | Diagrama de fiabilidad y Brier por fuente/modelo. |
| **Caso · Historial · Ajustes** | Editor de casos, informes en Markdown, roles, proveedores, notificaciones y actualizaciones. |

Los análisis largos corren en el servidor: **puedes bloquear el celular** y al volver el resultado está ahí. Si activas las notificaciones push, te avisa al terminar.

## Instalación (Windows)

1. Descarga **`Oraqlo-Setup-X.Y.Z.exe`** de la [última versión](https://github.com/ahiram1701/Oraqlo/releases/latest).
2. Ábrelo con doble clic y pulsa **Instalar**. No necesita permisos de administrador ni tener Python: trae lo suyo.
3. Al terminar, Oraqlo se abre en el navegador con un asistente de bienvenida. Ahí eliges el modelo de IA (Claude, OpenAI, Gemini, Ollama…) y pegas tu clave.

Windows puede mostrar el aviso azul *"Windows protegió tu PC"*, porque el instalador no está firmado con un certificado de pago. Pulsa **Más información → Ejecutar de todas formas**. Si quieres comprobar el archivo, su SHA256 está en `SHA256SUMS` y su procedencia se verifica con `gh attestation verify`.

Después:

- **Para abrirlo**, busca **Oraqlo** en el menú Inicio: se abre en **su propia ventana**, como cualquier programa, con su icono en la barra de tareas. El servidor arranca solo al iniciar sesión en Windows.
- **Al cerrar la ventana**, Oraqlo sigue en la **bandeja del sistema** (junto al reloj): los análisis continúan y te avisa con **notificaciones de Windows** cuando terminan o vence un hito. Desde el icono de la bandeja la vuelves a abrir o la abres en el navegador.
- **Para usarlo en el celular**, ve a **Ajustes → Conexión**. Te guía para instalar [Tailscale](https://tailscale.com/download), publica Oraqlo en tu red privada con un botón y te muestra un código QR. Ábrelo en el teléfono y elige **"Añadir a pantalla de inicio"**. La dirección es `https://<tu-pc>.<tu-tailnet>.ts.net:8443`: Oraqlo usa su propio puerto y nunca ocupa la dirección principal del PC, para no pisar otras apps que publiques con Tailscale.
- **Para desinstalarlo**, ve a **Configuración de Windows → Aplicaciones → Oraqlo → Desinstalar**. Te pregunta si quieres conservar tus datos.

No tienes que elegir puerto. Oraqlo usa el 8765 y, si otra aplicación lo ocupa, toma el siguiente libre; la dirección del celular no cambia. Lo mismo en la tailnet: si otra app usa el 8443, Oraqlo toma el siguiente. Si lo prefieres, puedes cambiar el puerto en **Ajustes → Conexión**.

### Todo se configura en Ajustes

| Qué | Dónde |
|---|---|
| Claves de API (se guardan solo en tu PC) | Ajustes → Proveedores y claves |
| Proveedor y modelo de cada rol del debate | Ajustes → Modelos del panel |
| Celular (Tailscale), puerto, quién puede entrar | Ajustes → Conexión |
| Avisos de Windows en este PC | Ajustes → Notificaciones |
| Actualizaciones automáticas / avisar / desactivadas; volver a la versión anterior | Ajustes → Sistema |
| Comprobar que todo funciona | Ajustes → Diagnóstico |
| Importar el historial de la versión de terminal | Ajustes → Importar historial antiguo |

Las claves y los ajustes del sistema solo se pueden cambiar desde el propio PC. Desde el celular se ven, pero no se editan.

### Actualizaciones automáticas

Oraqlo comprueba las releases cada 6 h. Cuando hay una versión nueva, la instala él solo:

1. Verifica el hash y la procedencia del paquete.
2. Prueba la versión nueva sobre una copia de tu base de datos.
3. Hace un backup de la base real.
4. Espera a que no haya ningún análisis en curso y la activa.

Si la versión nueva no arranca, **vuelve sola a la anterior** y te avisa.

### Instalación avanzada (PowerShell)

El `.exe` ejecuta por dentro [scripts/install-windows.ps1](scripts/install-windows.ps1), que también se puede usar a mano: necesita Python 3.12+ y admite `-ImportFrom`, `-Port`, `-NoTailscale` y `-Uninstall`.

```powershell
irm https://github.com/ahiram1701/Oraqlo/releases/latest/download/install-windows.ps1 -OutFile $env:TEMP\install-oraqlo.ps1
powershell -ExecutionPolicy Bypass -File $env:TEMP\install-oraqlo.ps1
```

## Proveedores de IA

Cada rol del panel (estratega, red_team, oraculo, juez, investigador, redactor, practica) puede usar **un proveedor y un modelo distinto**:

| Proveedor | Clave |
|---|---|
| Claude (Anthropic; por defecto `claude-opus-5-5`, con `effort` por rol) | `ANTHROPIC_API_KEY` |
| Ollama en el PC (gratis) / Ollama Cloud | — / `OLLAMA_API_KEY` |
| OpenAI, Gemini, OpenRouter, Groq, DeepSeek, Mistral, xAI | `OPENAI_API_KEY`, `GEMINI_API_KEY`, … |
| LM Studio o cualquier API compatible con OpenAI (`[providers.x]`) | la que definas |

Sin configuración, Oraqlo usa el primer proveedor cuya clave encuentre. Si no hay ninguna, usa Ollama local.

Por debajo, lo que guardas en Ajustes queda en dos archivos de tu carpeta de datos. Sirven para usos avanzados, como añadir proveedores propios:

- **`oraqlo.toml`**: ver [config/oraqlo.example.toml](config/oraqlo.example.toml). La app conserva tus comentarios al editarlo.
- **`.env`**: guarda las claves. Las claves nunca salen por la API.

La investigación web usa la API de búsqueda de Ollama (`OLLAMA_API_KEY`). Sin esa clave, Oraqlo avisa y sigue sin investigar.

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
