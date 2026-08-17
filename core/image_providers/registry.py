"""Registro de proveedores de imágenes de Space Lair.

Resuelve un proveedor de imágenes por nombre y, si no se indica ninguno,
usa el proveedor por defecto según la variable de entorno ``IMAGE_PROVIDER``.

Uso típico:

    from core.image_providers import get

    provider = get("local")            # o get() para usar IMAGE_PROVIDER
    result = provider.generate(prompt="una playa al atardecer")
    print(result.image_path, result.seed)
"""

from __future__ import annotations

import os
from typing import Type

from core.image_providers.base import ImageProvider, ImageProviderNotFoundError

# Proveedor por defecto si IMAGE_PROVIDER no está definido.
DEFAULT_PROVIDER = "comfyui"


class ImageProviderRegistry:
    """Contenedor de clases de proveedores de imágenes.

    Guarda las clases (no las instancias) para poder instanciarlas con la
    configuración del entorno vigente en el momento de la llamada a ``get``.
    """

    def __init__(self) -> None:
        self._classes: dict[str, Type[ImageProvider]] = {}
        self._default: str | None = None

    def register(self, provider_cls: Type[ImageProvider], default: bool = False) -> None:
        """Registra una clase de proveedor (por su atributo ``name``)."""
        if not (isinstance(provider_cls, type) and issubclass(provider_cls, ImageProvider)):
            raise TypeError("Solo se pueden registrar subclases de ImageProvider")
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

    def get(self, name: str | None = None) -> ImageProvider:
        """Instancia y devuelve un proveedor.

        Si ``name`` es None, usa la variable de entorno IMAGE_PROVIDER; si
        ésta no está definida, usa el proveedor por defecto del registro.
        """
        resolved = name or os.getenv("IMAGE_PROVIDER") or self._default or DEFAULT_PROVIDER
        if resolved not in self._classes:
            raise ImageProviderNotFoundError(
                f"Proveedor de imágenes '{resolved}' no registrado. "
                f"Disponibles: {self.names()}"
            )
        provider_cls = self._classes[resolved]
        return provider_cls(**provider_cls.env_config())


# Registro global compartido por toda la aplicación.
_registry: ImageProviderRegistry | None = None


def get_registry() -> ImageProviderRegistry:
    """Devuelve el registro global, creándolo e registrándo providers por defecto."""
    global _registry
    if _registry is None:
        _registry = ImageProviderRegistry()
        _register_defaults(_registry)
    return _registry


def _register_defaults(registry: ImageProviderRegistry) -> None:
    """Registra los proveedores incluidos con el paquete."""
    from core.image_providers.local import LocalImageProvider

    # ComfyUI es el proveedor por defecto (decisión de flip de DEFAULT_PROVIDER).
    # Se importa bajo demanda para evitar dependencias de red; si no está disponible,
    # local queda como default.
    try:
        from core.image_providers.comfyui import ComfyUiProvider

        registry.register(ComfyUiProvider, default=True)
    except Exception as e:  # pragma: no cover - el provider es opcional
        logger = _lazy_logger()
        logger.debug("ComfyUiProvider no disponible al registrar: %s", e)
    registry.register(LocalImageProvider)


def _lazy_logger():
    import logging

    return logging.getLogger("core.image_providers")


def register(provider_cls: Type[ImageProvider], default: bool = False) -> None:
    get_registry().register(provider_cls, default=default)


def unregister(name: str) -> None:
    get_registry().unregister(name)


def get(name: str | None = None) -> ImageProvider:
    """Devuelve un proveedor de imágenes instanciado (helper de acceso rápido)."""
    return get_registry().get(name)


def names() -> list[str]:
    return get_registry().names()
