"""Infraestructura de desarrollo autónomo supervisado para Space Lair.

Paquete de herramientas de orquestación: estado persistente, ejecución de
tests/E2E con captura, parseo de resultados y modos de agente (supervisado y
autónomo). NO modifica el comportamiento funcional de ningún módulo.
"""

__all__ = ["config", "state", "runner", "parsers", "security", "agent_loop", "autonomous"]