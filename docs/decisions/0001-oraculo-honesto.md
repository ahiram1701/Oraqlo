# ADR 0001 — Oráculo honesto: probabilístico, no "certeza"

**Estado:** aceptado · **Fecha:** 2026-07-15

## Contexto

La visión original pedía "un agente capaz de controlar la realidad, que pueda
predecirlo todo". Eso es físicamente inalcanzable: la realidad es caótica (sensibilidad
a condiciones iniciales), parcialmente observable (nunca tenemos el estado completo) y
adversarial (otros agentes optimizan contra ti). Un sistema diseñado como si la
omnisciencia fuera posible produce predicciones puntuales sin incertidumbre — es decir,
miente sobre su confianza, y sus usuarios toman decisiones peores que sin él.

## Decisión

Oraqlo emite **exclusivamente predicciones probabilísticas** (distribuciones,
intervalos, probabilidades de escenario), nunca valores puntuales "seguros". Cada
predicción se registra y, cuando llega el resultado real, se puntúa (Brier score /
log-loss) y alimenta la recalibración. La honestidad epistémica es un invariante del
sistema, no una opción de configuración.

## Consecuencias

- (+) Las recomendaciones llevan confianza cuantificada y supuestos explícitos: auditables.
- (+) El sistema mejora con el uso — la calibración es el bucle de aprendizaje.
- (−) Más fricción de UX: el usuario recibe "70% ± supuestos" en vez de "haz X".
- (−) Requiere disciplina de registro: sin cerrar predicciones con resultados reales,
  la calibración no aprende.
