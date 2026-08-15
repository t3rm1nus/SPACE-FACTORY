"""Bus de eventos de Space Lair.

Permite que el scheduler emita eventos (task_started, task_completed, etc.)
y que la API del frontend los reenvíe por SSE a los clientes.
"""

import logging
import threading
from typing import Any, Callable, Optional

from core.logger import get_logger, log

logger = get_logger(__name__)

# Suscriptores: {event_type: [callback, ...]}
_subscribers: dict[str, list[Callable]] = {}
_lock = threading.Lock()


def subscribe(event_type: str, callback: Callable) -> None:
    """Registra un callback para un tipo de evento."""
    with _lock:
        _subscribers.setdefault(event_type, []).append(callback)


def unsubscribe(event_type: str, callback: Callable) -> None:
    """Elimina un callback de un tipo de evento."""
    with _lock:
        if event_type in _subscribers:
            _subscribers[event_type] = [
                cb for cb in _subscribers[event_type] if cb != callback
            ]


def emit(event_type: str, data: Optional[dict[str, Any]] = None) -> None:
    """Emite un evento a todos los suscriptores."""
    data = data or {}
    with _lock:
        callbacks = list(_subscribers.get(event_type, []))

    for callback in callbacks:
        try:
            callback(event_type, data)
        except Exception as e:
            log(
                logger,
                logging.ERROR,
                f"Error en suscriptor de '{event_type}': {e}",
            )