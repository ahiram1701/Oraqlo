# ADR 0003 — Multi-proveedor LLM con adaptadores finos y salida estructurada única

**Estado:** aceptado · **Fecha:** 2026-11-15 · **Sustituye:** "Ollama como única dependencia"

## Contexto

Oraqlo hablaba solo con Ollama y la extracción del JSON del modelo estaba copiada en
tres sitios ("del primer `{` al último `}`"), sin esquemas ni reintentos de red.
La config de roles en YAML existía pero nunca se cargaba.

## Decisión

- Una interfaz (`LLMProvider.chat`) y **tres adaptadores finos** con SDKs oficiales:
  Anthropic, OpenAI-compatible (cubre OpenAI, Gemini, OpenRouter, Groq, DeepSeek,
  Mistral, xAI, LM Studio y proveedores propios) y Ollama. Sin capas tipo LiteLLM:
  menos superficie de dependencias y control total de cada API.
- Cada rol del panel elige proveedor, modelo y temperatura o `effort` en `oraqlo.toml`.
  Claude usa `effort` (no admite `temperature`).
- `llm/structured.py` es el único camino para JSON: esquema nativo si el proveedor
  lo soporta → extracción robusta → validación Pydantic → reintento con feedback.

## Consecuencias

- (+) Mezclar backends por rol (p. ej. Juez en Claude con effort alto, Red Team local).
- (+) Fallos de formato y de red tratados en un solo lugar.
- (−) Cada adaptador hay que mantenerlo cuando cambia su API (cubierto con tests de contrato).
