"""Compatibilidad: la salida estructurada vive en `oraqlo.llm.structured`."""

from oraqlo.llm.structured import JsonChatError, chat_json, chat_model, extract_json_object

__all__ = ["JsonChatError", "chat_json", "chat_model", "extract_json_object"]
