"""Registro de proveedores LLM de Space Lair.

Permite resolver un proveedor por nombre y, si no se indica ninguno, elegir el
proveedor por defecto según la variable de entorno LLM_PROVIDER.
"""

from __future__ import annotations

import os
import threading
from typing import Type

from core.providers.base import LLMProvider, LLMProviderNotFoundError

# Proveedor por defecto si LLM_PROVIDER no está definido.
DEFAULT_PROVIDER = "ollama"


class ProviderRegistry:
    """Contenedor de clases de proveedores LLM.

    Guarda las clases (no las instancias) para poder instanciarlas con la
    configuración de entorno vigente en el momento de la llamada a ``get``.
    """

    def __init__(self) -> None:
        self._classes: dict[str, Type[LLMProvider]] = {}
        self._default: str | None = None

    def register(self, provider_cls: Type[LLMProvider], default: bool = False) -> None:
        """Registra una clase de proveedor (por su atributo ``name``)."""
        if not (isinstance(provider_cls, type) and issubclass(provider_cls, LLMProvider)):
            raise TypeError("Solo se pueden registrar subclases de LLMProvider")
        name = provider_cls.name
        self._classes[name] = provider_cls
        if default or self._default is None:
            self._default = name

    def unregister(self, name: str) -> None:
        self._classes.pop(name, None)
        if self._default == name:
            self._default = next(iter(self._classes), None)

    def names(self) -> list[str]:
        return sorted(self._classes)

    def has(self, name: str) -> bool:
        return name in self._classes

    def default(self) -> str | None:
        return self._default

    def get(self, name: str | None = None) -> LLMProvider:
        """Instancia y devuelve un proveedor.

        Si ``name`` es None, usa la variable de entorno LLM_PROVIDER; si esta
        no está definida, usa el proveedor por defecto del registro.
        """
        resolved = name or os.getenv("LLM_PROVIDER") or self._default or DEFAULT_PROVIDER
        if resolved not in self._classes:
            raise LLMProviderNotFoundError(
                f"Proveedor LLM '{resolved}' no registrado. "
                f"Disponibles: {self.names()}"
            )
        provider_cls = self._classes[resolved]
        return provider_cls(**provider_cls.env_config())


# Registro global compartido por toda la aplicación.
# Se inicializa de forma lazy para evitar imports circulares y garantizar
# que el proveedor por defecto (ollama) esté disponible sin configuración previa.
_registry: ProviderRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> ProviderRegistry:
    """Devuelve el registro global, creándolo y registrando providers por defecto."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ProviderRegistry()
                try:
                    from core.providers.ollama import OllamaProvider
                    _registry.register(OllamaProvider, default=True)
                except Exception:
                    pass
    return _registry


def register(provider_cls: Type[LLMProvider], default: bool = False) -> None:
    get_registry().register(provider_cls, default=default)


def unregister(name: str) -> None:
    get_registry().unregister(name)


def get(name: str | None = None) -> LLMProvider:
    """Devuelve un proveedor instanciado (helper de acceso rápido)."""
    return get_registry().get(name)


def names() -> list[str]:
    return get_registry().names()
