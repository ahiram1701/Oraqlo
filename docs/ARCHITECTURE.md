# Oraqlo — Arquitectura

Motor de estrategia bajo incertidumbre, genérico y reutilizable. Este documento amplía
el diseño; los contratos ejecutables viven como ABCs en `src/oraqlo/`.

## Principios de diseño

1. **Calibración > confianza.** Toda predicción lleva una distribución o intervalo,
   nunca un valor "seguro". Se mide con Brier score / log-loss y se recalibra.
   (Ver [ADR 0001](decisions/0001-oraculo-honesto.md).)
2. **Modularidad enchufable.** Fuentes de datos, modelos de forecasting y LLMs son
   plugins detrás de interfaces. Cambiar de modelo Ollama = cambiar una línea de config.
3. **Auto-crítica adversarial.** Ninguna estrategia se emite sin pasar por un Red Team
   interno que intenta refutarla. El estratega piensa contra sí mismo.
4. **Incertidumbre explícita.** El sistema distingue lo que sabe, lo que estima y lo
   que ignora; marca supuestos y los degrada con el horizonte temporal.
5. **Trazabilidad.** Cada recomendación guarda su cadena de razonamiento, supuestos y
   confianza para auditarla después contra el resultado real.

## Bucle OODA

Ver [diagrams/oda-loop.md](diagrams/oda-loop.md). El agente (`agent.py`) orquesta:
Observe (conectores → world model) · Orient (forecaster + simulador) ·
Decide (planificador + panel LLM) · Act (emitir recomendación, registrar predicciones).

## Componentes y contratos

### 1. Ingesta / Percepción — `connectors/base.py`

`Connector` (ABC) normaliza observaciones heterogéneas (series, eventos, texto,
señales) hacia `Observation` con timestamp y fuente. Implementaciones previstas:
`FileConnector`, `APIConnector`, `ManualConnector`.

### 2. World Model — `world_model.py`

Representación estructurada y agnóstica al dominio: `Entity`, `Variable` (con tipo e
incertidumbre) y `WorldState` versionado en el tiempo (permite rebobinar y
contrafactuales). Única fuente de verdad para los demás módulos.

### 3. Forecaster (el oráculo) — `forecaster/base.py`

Produce **distribuciones** (`Distribution`) sobre estados futuros combinando:

- *Cuantitativo:* series temporales / Bayes para variables numéricas (intervalos).
- *Cualitativo:* razonamiento LLM (Ollama) para variables sin datos, generación de
  escenarios y probabilidades subjetivas.
- *Ensamble + calibración:* combina fuentes y ajusta con el histórico de aciertos.

### 4. Simulador de escenarios — `simulator/base.py`

Expande futuros a partir de las distribuciones del Forecaster:

- `MonteCarloEngine` — muestrea trayectorias para propagar incertidumbre.
- `TreeSearchEngine` — explora secuencias de decisiones varios pasos por delante (MCTS).
- `AdversaryModel` — para dominios competitivos: asume que el oponente también
  optimiza (minimax / teoría de juegos). Esto hace de Oraqlo un estratega, no solo
  un pronosticador.

### 5. Planificador — `strategy/planner.py`

Sobre los escenarios simulados calcula la acción de mayor **utilidad esperada ajustada
a riesgo**: no solo la media — penaliza cola/ruina (CVaR), aversión al riesgo
configurable, objetivos multi-criterio y restricciones duras.

### 6. Memoria + Calibración — `memory/calibration.py`

El bucle de aprendizaje: registra cada predicción con su probabilidad; al llegar el
resultado real calcula Brier/log-loss por fuente y ajusta pesos y calibración.
Memoria episódica (observaciones + predicciones + resultados) y semántica (patrones).

**Brier score** para un evento binario con probabilidad predicha `p` y resultado
`o ∈ {0,1}`: `BS = (p − o)²`. Media sobre N predicciones; 0 = perfecto, 0.25 = azar
con p=0.5 fijo.

### 7. Panel de decisión — `panel.py`

Debate interno multi-rol (cada rol es un LLM vía Ollama, configurable por rol):

| Rol | Función | Temperatura típica |
|---|---|---|
| **Estratega** | propone el plan y su tesis | media-alta |
| **Red Team** | ataca el plan, busca supuestos frágiles | alta |
| **Oráculo** | asigna probabilidades a objeciones y éxito | baja |
| **Juez** | sintetiza, decide, fija confianza y disparadores de revisión | muy baja |

## Capa LLM: Ollama local + cloud — `llm/`

`LLMProvider` (ABC) con una única implementación `OllamaProvider` que habla el mismo
protocolo contra dos backends elegidos por config:

- **Local:** `http://localhost:11434`, sin credenciales. Privado, gratis, offline.
- **Cloud (Ollama Turbo):** `https://ollama.com` + API key leída de la variable de
  entorno `OLLAMA_API_KEY` (nunca hardcodeada). Acceso a modelos grandes
  (`gpt-oss:120b-cloud`, `qwen3-coder:480b-cloud`, …).

El resto del sistema no distingue local de cloud: solo cambian `host` y la cabecera de
autorización. Los roles pueden mezclar backends (`config/roles.example.yaml`).
Ollama es la única dependencia de inferencia — una clase, un protocolo, sin ramas
divergentes.

## Fuera de alcance (explícito)

- **No hay "control de la realidad":** Oraqlo recomienda y prevé; actuar en el mundo
  real queda a cargo de actuadores que el usuario integre, con humano en el bucle.
- El QuantForecaster (series temporales/Bayes) y el EnsembleForecaster quedan
  pendientes; el LLMForecaster cualitativo, el ledger de calibración, los motores
  de simulación (Monte Carlo y expectimax), el planificador EU+CVaR, el panel y el
  ciclo OODA del agente están implementados y testeados.
- El `TreeSearchEngine` es expectimax exacto: suficiente para árboles pequeños;
  árboles grandes necesitarán MCTS con presupuesto (la interfaz ya lo admite).
- El `SoftmaxAdversary` existe como pieza independiente; su integración dentro de
  los motores de simulación (turnos alternos) queda para la siguiente fase.
- Sin promesas de exactitud: el valor es la calibración y la disciplina estratégica.

## Verificación

1. **Import sanity:** los módulos importan sin error; `tests/test_smoke.py` verifica
   que las ABCs existen y que los métodos abstractos sin implementar lanzan
   `NotImplementedError` / `TypeError`.
2. **Prueba real de Ollama:** `examples/hello_oracle.py --backend local|cloud` manda
   un prompt de escenarios a un modelo real y muestra la respuesta. Si el backend no
   está disponible, avisa con un mensaje claro en vez de fallar con stack trace.
