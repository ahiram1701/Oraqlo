# Bucle OODA de Oraqlo

```mermaid
flowchart TD
    SRC[Fuentes de datos] --> ING["1. Ingesta / Percepción<br/>(connectors)"]
    ING --> WM["2. World Model<br/>estado estructurado + memoria"]
    WM --> FC["3. Forecaster (oráculo)<br/>ML + Bayes + LLM"]
    WM --> SIM["4. Simulador<br/>Monte Carlo · MCTS · adversario"]
    FC <--> SIM
    FC --> PLAN["5. Planificador<br/>utilidad esperada + riesgo"]
    SIM --> PLAN
    CAL["6. Memoria + Calibración<br/>Brier / feedback"] --> PLAN
    CAL --> FC
    PLAN --> PANEL["7. Panel de decisión LLM<br/>Estratega ▸ Red Team ▸ Oráculo ▸ Juez"]
    PANEL --> OUT["Recomendación ranqueada<br/>+ confianza + supuestos"]
    OUT -. "resultado real (cuando llega)" .-> CAL
```

- **Observe:** Fuentes → Ingesta → World Model.
- **Orient:** Forecaster ⇄ Simulador (futuros probables y contramovimientos).
- **Decide:** Planificador → Panel (debate adversarial interno).
- **Act:** emitir recomendación; registrar cada predicción en Calibración para
  puntuarla cuando llegue el resultado real — el bucle de aprendizaje.
