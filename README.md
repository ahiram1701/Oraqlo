# Oraqlo

**Motor de estrategia bajo incertidumbre** — un agente estratega general, genérico y
reutilizable, que mantiene un modelo del mundo, genera previsiones probabilísticas
calibradas, simula futuros y oponentes, y recomienda la jugada de mayor utilidad
esperada ajustada a riesgo.

## Lo que Oraqlo NO es (límites honestos)

Oraqlo no "controla la realidad" ni "predice todo". Ningún sistema puede: la realidad
es caótica, parcialmente observable y adversarial. Un sistema que finge certeza miente
sobre su confianza — lo peor que puede hacer un estratega.

La fuerza de Oraqlo es **cuantificar lo que no sabe**: toda predicción lleva una
distribución de probabilidad, se mide contra los resultados reales (Brier score) y se
recalibra. Es un oráculo honesto.

## Arquitectura (bucle OODA)

```
Fuentes → Ingesta → World Model → Forecaster ⇄ Simulador → Planificador → Panel LLM → Recomendación
                        ↑                                        │
                        └──────── Memoria + Calibración ◀────────┘  (predicción vs resultado)
```

- **World Model** — estado estructurado del dominio, versionado en el tiempo.
- **Forecaster** — distribuciones sobre futuros (ML + Bayes + razonamiento LLM).
- **Simulador** — Monte Carlo, búsqueda en árbol, modelo del adversario.
- **Planificador** — utilidad esperada ajustada a riesgo, multi-criterio.
- **Memoria + Calibración** — Brier/log-loss por fuente; el bucle de aprendizaje.
- **Panel LLM** — debate interno: Estratega ▸ Red Team ▸ Oráculo ▸ Juez.

Ver [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) para el diseño completo.

## LLMs vía Ollama (local + cloud)

Una sola implementación (`OllamaProvider`) habla el mismo protocolo contra dos backends:

- **Local:** `http://localhost:11434`, sin credenciales.
- **Cloud (Ollama Turbo):** `https://ollama.com` + API key en la variable de entorno
  `OLLAMA_API_KEY` (nunca hardcodeada).

Cada rol del panel se mapea a un modelo y backend vía [config/roles.example.yaml](config/roles.example.yaml)
— se pueden mezclar (p. ej. Juez en cloud, Red Team en local).

## Requisitos e instalación

- **Python ≥ 3.12**.
- **Ollama** para la inferencia: [local](https://ollama.com) (gratis, offline) o cloud
  (Ollama Turbo). Para cloud, exporta tu clave: `OLLAMA_API_KEY`.
- El **núcleo no tiene dependencias** (solo librería estándar). Los extras son opcionales:

```bash
git clone <url-del-repo> && cd Oraqlo
pip install -e ".[tui]"        # instala Textual (para la TUI)
pip install -e ".[tui,dev]"    # además pytest (para correr los tests)
```

Los ejemplos de `examples/` funcionan sin instalar nada (usan solo la stdlib).

## Quickstart

```bash
# sin dependencias externas: solo la librería estándar de Python 3.12+
python examples/hello_oracle.py --backend local            # necesita Ollama corriendo
OLLAMA_API_KEY=... python examples/hello_oracle.py --backend cloud --model gpt-oss:120b-cloud

# bucle de aprendizaje completo: predecir → registrar → resolver → Brier
python examples/learning_loop.py --backend cloud

# debate del panel: Estratega ▸ Red Team ▸ Oráculo ▸ Juez con LLMs reales
python examples/panel_debate.py --backend cloud

# ciclo OODA completo: observar → prever → simular → planificar → deliberar
python examples/full_cycle.py --backend cloud
```

## TUI (interfaz de terminal)

```bash
pip install textual
python tui.py                        # abre con la plantilla de caso
python tui.py cases/cafeterias.json  # abre con un caso concreto
```

Seis pestañas:

- **Preguntar** — el camino corto: **escribe tu pregunta y pulsa Enter** (previsión
  rápida, 1 llamada LLM) o "Análisis completo" (el LLM redacta el caso entero —
  acciones candidatas, escenarios, utilidades — y ejecuta el ciclo con panel).
  El caso redactado queda cargado en la pestaña Caso por si quieres afinarlo.
  Y **"Lograr objetivo"**: escribe lo que quieres conseguir y Oraqlo hace
  backcasting — estima P(objetivo) si no cambias nada, separa **qué puedes
  hacer** (palancas con impacto/esfuerzo) de **qué tiene que pasar** (condiciones
  externas con probabilidad), diseña **qué tienes que hacer** (plan con pasos e
  hitos fechados), lo somete al Red Team (y lo re-diseña si el Juez lo pide), y
  mide el **uplift**: P(objetivo | plan) − P(statu quo). Ambas predicciones
  quedan en el ledger — al vencer, resuélvelas y Oraqlo aprende si sus planes
  de verdad mueven la probabilidad. Si el uplift sale ≤ 0, lo dice tal cual.
  Con **"Investigar en internet"** (activado por defecto), Oraqlo primero busca
  en la web (API de búsqueda de Ollama, misma `OLLAMA_API_KEY`): un rol
  investigador genera las consultas **sabiendo la fecha de hoy**, y tras cada
  ronda **reflexiona** — si falta un dato concreto busca más, si una fuente
  merece leerse completa la lee a fondo (solo URLs surgidas de la búsqueda),
  y si ya basta sintetiza (máx. 3 rondas). Los hechos, con su URL, entran al
  world model y al contexto del panel — las probabilidades se apoyan en datos
  actuales, no solo en la memoria del modelo. Sin API key o si la búsqueda
  falla, avisa y continúa sin investigación.
- **Caso** — formulario guiado (pregunta, acciones con sus resultados/probabilidades/
  utilidades, perfil de riesgo) con **validación en vivo** (Σp por acción en
  verde/rojo). "Modo JSON" conmuta al editor avanzado, sincronizado en ambos
  sentidos. `cases/cafeterias.json` es un ejemplo completo.
- **Ejecutar** — backend (cloud/local) y **selector de modelos** ("↻ Modelos"
  consulta los disponibles de verdad). "Solo previsión" (1 llamada LLM) o "Ciclo
  completo". **Debate del panel en vivo rol a rol**, botones deshabilitados e
  indicador mientras corre, duración por fase, y **"Exportar informe"** que
  escribe el rastro completo (previsión, ranking, debate, veredicto) a
  `informes/<fecha>-<caso>.md`.
- **Ledger** — predicciones abiertas **persistidas en `oraqlo_ledger.json`**:
  selecciona una y sus **escenarios aparecen como opciones** (más "otro
  resultado…" libre) — resolver ya no exige teclear el texto exacto. El Brier
  alimenta la fiabilidad por fuente entre sesiones. También puedes **Eliminar**
  una abierta (se descarta sin puntuar, no afecta a la calibración) y, en la
  tabla de **Resueltas**, eliminar entradas del historial — con confirmación,
  porque borra datos de calibración y la fiabilidad se recalcula sin ellas.
- **Objetivos** — **seguimiento para lograr lo planeado**. Cada "Lograr objetivo"
  queda en seguimiento automático (persistido en `oraqlo_goals.json`): hitos con
  fecha y estado (pendiente/cumplido/fallado, con aviso de vencidos al abrir la
  TUI), **check-ins** ("semana 1: 3 entrenamientos, 28 km") que re-estiman
  P(objetivo) — la **trayectoria de P en el tiempo** es tu señal de progreso —,
  **re-planificación** cuando un hito falla o la trayectoria cae (conserva lo
  cumplido, corrige lo demás, versiona el plan), **cierre** logrado/no logrado
  que resuelve las dos predicciones originales del ledger (Oraqlo aprende si sus
  planes funcionan de verdad), y **eliminar** un objetivo por completo (con
  confirmación): sus predicciones abiertas se descartan sin puntuar y las ya
  resueltas se conservan.
- **Práctica** — **auto-mejora autónoma con Polymarket** (solo lectura: cero
  cuentas, cero apuestas). Oraqlo elige mercados reales que cierran pronto,
  predice sin ver el precio, registra también el precio del mercado como
  benchmark (`polymarket:mercado`), y cuando cada mercado se resuelve cierra
  ambas predicciones solo y compara Brier: ¿va mejor o peor que la multitud?
  Del historial resuelto deriva un **consejo de calibración** ("sobreconfías:
  reparte más la probabilidad") que se inyecta a las siguientes predicciones —
  el mecanismo concreto de auto-mejora. El Switch "Autónoma" repite pasadas
  cada N minutos mientras la TUI esté abierta (apagado por defecto: cuesta
  llamadas LLM). **El estado del switch y el intervalo persisten** en
  `oraqlo_settings.json`: si lo dejas activado, la próxima vez que abras la TUI
  se reanuda solo (armando el temporizador, sin pasada inmediata — abrir la
  app no cuesta llamadas); si lo apagas, arranca apagado.

  Sin TUI (headless / programable):

  ```bash
  set PYTHONPATH=src
  python -m oraqlo.autopractice --markets 2              # una pasada
  python -m oraqlo.autopractice --loop --interval-min 60 # bucle continuo
  ```

  Para autonomía total, prográmalo en el Programador de tareas de Windows:
  acción `python`, argumentos `-m oraqlo.autopractice --markets 2`, iniciar en
  `C:\DEV\tests\Oraqlo` con la variable `PYTHONPATH=src`, cada hora.

Tests de humo:

```bash
python -m pytest tests/ -q     # o: python tests/test_smoke.py
```

## Estado

**Todo el bucle OODA es funcional de punta a punta** (`examples/full_cycle.py`):

- `llm/ollama.py` — proveedor local + cloud (Turbo) con errores accionables.
- `forecaster/llm.py` — LLMForecaster con JSON validado, normalización y reintento
  con feedback.
- `memory/calibration.py` — Brier binario/multiclase, log-loss, `CalibrationLedger`
  completo (`resolve`/`reliability`).
- `panel.py` — `SequentialPanel`: debate adversarial de 4 roles con transcript trazable.
- `world_model.py` — estados versionados e inmutables, `apply`/`at` (rebobinado).
- `simulator/` — `MonteCarloEngine` (rollouts reproducibles), `TreeSearchEngine`
  (expectimax exacto con continuación óptima), `SoftmaxAdversary` (oponente
  racional-con-ruido).
- `strategy/planner.py` — `ExpectedUtilityPlanner`: EU + CVaR, descarte por riesgo
  de ruina (se niega a recomendar si todo es ruina), restricciones como supuestos.
- `agent.py` — `OraqloAgent.cycle()`: el ciclo completo, con redeliberación ante
  "revisar" (objeciones inyectadas al contexto) y fallback a la siguiente candidata
  ante "descartar".

**Pendiente para producción:** QuantForecaster (series temporales/Bayes),
EnsembleForecaster ponderado por `reliability()`, MCTS con presupuesto para árboles
grandes, e integración del `AdversaryModel` dentro de los motores de simulación.

## Tests

```bash
pip install -e ".[tui,dev]"
pytest -q
```

La suite (79 tests) corre **100% offline**: las llamadas a LLM, la búsqueda web y
Polymarket están simuladas con dobles de test, así que no necesita red ni
`OLLAMA_API_KEY`. Se ejecuta también en CI (GitHub Actions) sobre Python 3.12 y 3.13.

## Privacidad

Tu estado vive en archivos JSON locales (`oraqlo_ledger.json`, `oraqlo_goals.json`,
`oraqlo_settings.json`) y los informes en `informes/`. El `.gitignore` los excluye para
que **tus datos personales nunca se suban** al repositorio. La `OLLAMA_API_KEY` se lee de
la variable de entorno y jamás se guarda en disco ni en el código.

## Licencia

© 2026 Alberto H. Saucedo G. Proyecto **sin licencia de código abierto**: todos los
derechos reservados. Contacta al autor para permisos de uso.
