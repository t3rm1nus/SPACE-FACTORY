"""Proveedores de generación de imágenes de Space Lair.

Expone el registro global y el helper ``get`` para resolver un proveedor
por nombre (o el de la variable de entorno ``IMAGE_PROVIDER``).
"""

from __future__ import annotations

from core.image_providers.base import (
    ImageConnectionError,
    ImageInvalidResponseError,
    ImageModelNotFoundError,
    ImageProvider,
    ImageProviderError,
    ImageProviderNotFoundError,
    ImageResult,
    ImageTimeoutError,
)
from core.image_providers.registry import (
    get,
    get_registry,
    register,
    unregister,
)

__all__ = [
    "get",
    "get_registry",
    "register",
    "unregister",
    "ImageProvider",
    "ImageProviderError",
    "ImageProviderNotFoundError",
    "ImageConnectionError",
    "ImageTimeoutError",
    "ImageModelNotFoundError",
    "ImageInvalidResponseError",
    "ImageResult",
]
