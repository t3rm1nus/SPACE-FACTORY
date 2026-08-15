"""Proveedor de imágenes LOCAL de Space Lair.

Generación offline sin dependencias externas. No invoca ningún modelo real:
produce un PNG válido de color uniforme cuyo tono se deriva de la semilla y
el prompt (hash). Útil como provider por defecto para tests, previews y como
garantía de disponibilidad sin GPU ni servidor ComfyUI.

Configuración (variables de entorno):

    IMAGE_PROVIDER=local
    IMAGE_LOCAL_OUTPUT_DIR=./data/images/local   (carpeta donde se guardan los PNG)
    IMAGE_MODEL=<modelo>                          (opcional, para reporte)
    IMAGE_SEED=<int>                              (semilla por defecto)
    IMAGE_STEPS=<int>                             (pasos por defecto)

Salida de ``generate``:

    ImageResult(image_path, provider, model, seed, cost, metadata, raw_response)
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Optional

from core.image_providers.base import (
    ImageProvider,
    ImageProviderError,
    ImageResult,
    _env,
    _env_int,
)

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/images/local"


def _resolve_output_dir(output_dir: Optional[str]) -> str:
    base = output_dir or _env("IMAGE_LOCAL_OUTPUT_DIR", DEFAULT_OUTPUT_DIR)
    os.makedirs(base, exist_ok=True)
    return base


def _seed_color(seed: int) -> tuple[int, int, int]:
    """Deriva un color RGB estable a partir de una semilla entera."""
    digest = hashlib.sha256(f"{seed}".encode("utf-8")).digest()
    r = min(255, digest[0] + 40)
    g = min(255, digest[1] + 40)
    b = min(255, digest[2] + 40)
    return r, g, b


def _write_placeholder_png(path: str, width: int, height: int, seed: int) -> str:
    """Escribe un PNG de color sólido (derivado de la semilla) usando solo stdlib.

    Formato PNG mínimo: cada fila se filtra con filtro ``None`` (0) y los
    datos se comprimen con ``zlib``. No requiere Pillow ni dependencias externas.

    Returns:
        La ruta del archivo escrito.
    """
    import struct
    import zlib

    if width <= 0 or height <= 0:
        raise ImageProviderError(f"Dimensiones inválidas: {width}x{height}")

    r, g, b = _seed_color(seed)
    raw = bytearray()
    for _ in range(height):
        raw.append(0)  # filtro None por fila
        raw.extend(bytes((r, g, b, 255)) * width)
    compressed = zlib.compress(bytes(raw), level=9)

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + chunk_type
            + data
            + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += _chunk(b"IDAT", compressed)
    png += _chunk(b"IEND", b"")

    if not path.lower().endswith(".png"):
        path = path + ".png"
    with open(path, "wb") as f:
        f.write(png)
    return path


class LocalImageProvider(ImageProvider):
    """Proveedor local que genera PNG placeholder sin dependencias."""

    name = "local"

    def __init__(
        self,
        model: Optional[str] = None,
        *,
        output_dir: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **_: Any,
    ) -> None:
        super().__init__(model=model, timeout=timeout, max_retries=max_retries)
        self.model = model or self._default_model()
        self.output_dir = _resolve_output_dir(output_dir or _env("IMAGE_LOCAL_OUTPUT_DIR"))

    @classmethod
    def _default_model(cls) -> str:
        return _env("IMAGE_MODEL") or "placeholder"

    @classmethod
    def env_config(cls) -> dict:
        return {
            "model": _env("IMAGE_MODEL"),
            "output_dir": _env("IMAGE_LOCAL_OUTPUT_DIR"),
            "timeout": _env_int("IMAGE_TIMEOUT", 120),
            "max_retries": _env_int("IMAGE_MAX_RETRIES", 2),
        }

    def _generate_once(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        steps: int,
        seed: int,
        model: str,
        aspect_ratio: str,
        **kwargs: Any,
    ) -> ImageResult:
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
        filename = f"{seed}_{prompt_hash}.png"
        path = os.path.join(self.output_dir, filename)
        _write_placeholder_png(path, width, height, seed)

        logger.debug(
            "LocalImageProvider generó placeholder %s (%dx%d, seed=%d)",
            path, width, height, seed,
        )
        return ImageResult(
            image_path=path,
            provider=self.name,
            model=model,
            seed=seed,
            cost=0.0,
            raw_response=None,
            metadata={
                "width": width,
                "height": height,
                "steps": steps,
                "aspect_ratio": aspect_ratio,
                "negative_prompt": negative_prompt,
                "prompt_hash": prompt_hash,
                "color": _seed_color(seed),
            },
        )

    def health_check(self) -> dict:
        writable = False
        sample_path = os.path.join(self.output_dir, ".health")
        try:
            with open(sample_path, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(sample_path)
            writable = True
        except OSError as e:
            logger.warning("LocalImageProvider: carpeta no escribible: %s", e)
        return {
            "provider": self.name,
            "model": self.model,
            "output_dir": self.output_dir,
            "writable": writable,
            "healthy": writable,
            "status": "🟢 healthy (local)" if writable else "🔴 unhealthy",
        }

    def available_models(self) -> list:
        return ["placeholder", "solid"]

    def _metadata_extras(self) -> dict:
        return {
            "output_dir": self.output_dir,
            "available_models": self.available_models(),
        }


def is_valid_png(path: str) -> bool:
    """Comprueba que ``path`` apunta a un archivo PNG válido por magic bytes."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            return f.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False

