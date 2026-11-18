# ADR 0005 — Más voces, vigilancia y puntos de ruptura

**Estado:** aceptado · **Fecha:** 2026-11-18

## Contexto

Oraqlo predecía bien solo con lo que le preguntabas y en el momento en que lo
preguntabas. Tres huecos le impedían dar más control real sobre lo que pasa:

1. **Precisión.** Ignoraba la mejor estimación pública disponible: el precio de los
   mercados de predicción. No usaba su propio historial como clase de referencia,
   estimaba de golpe las preguntas compuestas y corregía la sobreconfianza con un
   único factor para todo. Además, solo la Práctica registraba lo que decía cada
   miembro del comité.
2. **Tiempo.** Una previsión quedaba congelada aunque el mundo cambiara al día siguiente.
3. **Decisión.** Recomendaba una acción sin decir cuánto podía equivocarse en cada
   cifra antes de que la recomendación dejara de ser la buena, ni qué convenía averiguar
   antes de decidir. Además, suponía oponentes pasivos.

## Decisión

- **Voces extra del comité.** El mercado (`mercado:<plataforma>`) y la estimación por
  partes (`estructura`) entran en `committee.combine` como un modelo más, con un peso
  aprendido de su historial, igual que los demás. Una voz mala pierde peso sola. El
  mercado solo cuenta si un modelo confirma que pregunta **exactamente** lo mismo; ante
  la duda, no hay coincidencia. La Práctica sigue a ciegas.
- **Lo que dice cada voz se registra en todas las previsiones**, no solo en la
  Práctica (`<id>::<voz>`, fuera del boletín), para que los pesos aprendan del uso real.
- **Calibración por tipo de pregunta**: un factor por grupo (tipo · binaria/múltiple
  · plazo). Se encoge hacia el factor global y se activa solo si mejora en validación
  cruzada.
- **Preguntas de cantidad** como intervalos contiguos. Se puntúan con el mismo Brier y
  se resuelven escribiendo el número.
- **Vigilancia**: cada previsión u objetivo guarda señales observables (del Vigía,
  del pre-mortem o de los avisos del Juez). Una tarea periódica con presupuesto diario
  investiga lo nuevo y, si importa, vuelve a predecir. La versión nueva se **encadena** a
  la anterior y al resolver se puntúan todas: así se premia actualizar bien.
- **Puntos de ruptura y valor de la información** sobre el caso, con el mismo
  planificador: a partir de qué probabilidad o utilidad cambiaría la mejor acción, y
  cuánto vale saber de antemano cómo saldría cada una.
- **Rival opcional** en los casos (`SoftmaxAdversary`). Quien reacciona elige según su
  propia utilidad; nunca se le supone pasivo.
- **Sin migraciones**: la vigilancia vive en `settings` y las etiquetas, en `assumptions`.
  La vuelta atrás automática del actualizador sigue funcionando.

## Consecuencias

- (+) Más precisión donde hay mercado y en preguntas compuestas. Ninguna mejora puede
  empeorar mucho la cifra: su peso depende de cuánto acierte.
- (+) Las previsiones siguen vivas y avisan. El usuario sabe qué vigilar y qué averiguar.
- (−) Más llamadas al modelo y a internet: hay interruptores en Ajustes, presupuesto
  diario y ninguna llamada extra si la pregunta es simple o no hay mercado.
- (−) Cada versión vigilada puntúa: una pregunta muy cambiante pesa más en el boletín.
  Es intencionado, porque actualizar bien es parte de predecir bien.
- (Honestidad, ADR 0001) Nada de esto «controla la realidad». Oraqlo da más control
  sobre las probabilidades: sabe mejor qué pasará, se entera antes de que cambie y
  sabe qué palanca la mueve.
