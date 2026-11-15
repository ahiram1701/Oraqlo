# ADR 0002 — App web local + Tailscale en lugar de TUI

**Estado:** aceptado · **Fecha:** 2026-11-15

## Contexto

La interfaz era una TUI (Textual). El uso real de Oraqlo es móvil e intermitente:
check-ins de objetivos, resolver predicciones cuando vencen, mirar la trayectoria
de P(objetivo). Además, la TUI y el CLI de práctica escribían los mismos JSON sin
coordinación, y la práctica autónoma solo corría "mientras la TUI estuviera abierta".

## Decisión

- Servidor FastAPI en este PC, escuchando **solo en 127.0.0.1**, con frontend React
  instalable como **PWA**.
- Acceso remoto exclusivamente por **`tailscale serve`**: HTTPS con certificado
  MagicDNS y autenticación de dispositivo gestionada por Tailscale. La identidad llega
  en `Tailscale-User-Login`; se rechaza el tráfico de proxy sin identidad y Funnel.
- Todo lo lento es un **job** persistido con eventos reanudables por SSE.
- La TUI se retira.

## Consecuencias

- (+) Celular como cliente de primera clase, sin abrir puertos ni gestionar login.
- (+) Trabajos que sobreviven a pantallas bloqueadas; tareas autónomas en el servidor.
- (+) HTTPS real habilita PWA instalable y Web Push.
- (−) El PC tiene que estar encendido (mitigado: no se suspende con trabajos activos).
- (−) Dos stacks (Python + TypeScript) que mantener.
