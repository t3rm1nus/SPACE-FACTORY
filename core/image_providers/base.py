"""Base de la capa de proveedores de imágenes de Space Lair.

Define la interfaz común ``ImageProvider``, la estructura de respuesta
estándar ``ImageResult`` y una jerarquía de excepciones, junto con
utilidades compartidas (timeout, reintentos, cálculo de coste reutilizando
``core.metrics``).

Los proveedores disponibles son:

- ``local``:       generación local sin dependencias externas (placeholder).
- ``comfyui``:     cliente del API HTTP de ComfyUI (difusión local).
- ``openai_compatible``: genérico para APIs estilo OpenAI (imágenes).

Uso típico:

    from core.image_providers import get

    provider = get("local")              # o get() para usar IMAGE_PROVIDER
    result = provider.generate(
        prompt="un río desembocando en el Atlántico",
        negative_prompt="texto, marcas de agua",
        width=1024, height=576, steps=20, seed=42, model="default",
    )
    print(result.image_path, result.seed)
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# Resoluciones de aspecto admitidas por el ecosistema de imágenes.
VALID_ASPECT_RATIOS = ("16:9", "3:2", "4:3", "1:1", "2:3", "9:16")


# ----------------------------------------------------------------------------
# Jerarquía de excepciones
# ----------------------------------------------------------------------------
class ImageProviderError(Exception):
    """Error genérico de la capa de proveedores de imágenes."""


class ImageConnectionError(ImageProviderError):
    """No se pudo conectar al proveedor (refused, red, HTTP >= 500...)."""


class ImageTimeoutError(ImageProviderError):
    """El proveedor no respondió dentro del timeout configurado."""


class ImageInvalidResponseError(ImageProviderError):
    """La respuesta del proveedor no era válida."""


class ImageModelNotFoundError(ImageProviderError):
    """El modelo solicitado no existe en el proveedor."""


class ImageProviderNotFoundError(ImageProviderError):
    """No hay un proveedor registrado con ese nombre."""


# ----------------------------------------------------------------------------
# Respuesta estándar
# ----------------------------------------------------------------------------
@dataclass
class ImageResult:
    """Respuesta normalizada de cualquier proveedor de imágenes.

    Campos:
        image_path:   ruta local al archivo de imagen generado.
        provider:     nombre del proveedor (\"local\", \"comfyui\", ...).
        model:        modelo usado para la generación.
        seed:         semilla exacta usada (reproducible).
        cost:         coste estimado en USD (0.0 si es local/desconocido).
        raw_response: respuesta cruda del proveedor (opcional).
        metadata:     contexto adicional (tamaño, steps, etc.).
    """

    image_path: str
    provider: str
    model: str
    seed: int
    cost: float = 0.0
    raw_response: Any = None
    metadata: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------
# Utilidades de entorno y red
# ----------------------------------------------------------------------------
def _env(name: str, default: Any = None) -> Any:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, default))
    except (TypeError, ValueError):
        return default


def _delay_backoff(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """Backoff exponencial para reintentos (0.5, 1, 2, 4, 8...)."""
    return min(base * (2 ** max(attempt, 0)), cap)


def http_json(
    method: str,
    url: str,
    *,
    payload: Optional[dict] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 30.0,
) -> Any:
    """Realiza una petición HTTP y devuelve el JSON decodificado.

    Reúne las excepciones de red/HTTP bajo la jerarquía ``ImageProviderError``
    mediante ``map_urlerror``.
    """
    import json as _json

    headers = dict(headers or {})
    data: Optional[bytes] = None
    if payload is not None:
        headers.setdefault("Content-Type", "application/json")
        data = _json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers=headers, method=method.upper())
    try:
        resp = urlopen(req, timeout=timeout)
    except (HTTPError, URLError, socket.timeout, TimeoutError) as e:
        raise map_urlerror(e) from e
    try:
        raw = resp.read()
    finally:
        resp.close()
    try:
        return _json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise ImageInvalidResponseError(f"Respuesta no JSON de {url}: {e}") from e


def map_urlerror(e: Exception) -> ImageProviderError:
    """Mapea excepciones de red/HTTP/urllib a la jerarquía ``ImageProvider*``.

    - 404 / \"model not found\"     -> ImageModelNotFoundError
    - timeout                      -> ImageTimeoutError
    - red / refused / HTTP >= 500  -> ImageConnectionError
    - resto (4xx no esperado, parse) -> ImageInvalidResponseError
    """
    code: Optional[int] = None
    if isinstance(e, HTTPError):
        code = e.code
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
    else:
        body = str(e)

    text = body.lower()
    if isinstance(e, (socket.timeout, TimeoutError)) or "timeout" in text:
        return ImageTimeoutError(str(e))
    if code == 404 or "not found" in text or "model" in text:
        return ImageModelNotFoundError(str(e))
    if isinstance(e, HTTPError) and code is not None and code >= 500:
        return ImageConnectionError(f"HTTP {code}: {body}")
    if isinstance(e, (URLError, ConnectionError, socket.error)):
        return ImageConnectionError(str(e))
    return ImageInvalidResponseError(str(e))


# ----------------------------------------------------------------------------
# Proveedor base (interfaz común)
# ----------------------------------------------------------------------------
class ImageProvider:
    """Interfaz común que deben cumplir todos los proveedores de imágenes.

    Campos/atributos:
        name:   identificador del proveedor (\"local\", \"comfyui\", ...).
        model:  modelo por defecto configurado.

    Métodos principales:
        generate(...)       -> ImageResult
        health_check()      -> dict
        available_models()  -> list

    Los reintentos se aplican solo a errores transitorios (timeout y
    problemas de conexión/red). Un modelo inexistente o una respuesta inválida
    se propagan de inmediato.
    """

    name: str = "base"

    def __init__(
        self,
        model: Optional[str] = None,
        *,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **_: Any,
    ) -> None:
        self.model = model or self._default_model()
        self.timeout = float(
            timeout if timeout is not None else _env_int("IMAGE_TIMEOUT", 120)
        )
        self.max_retries = int(
            max_retries if max_retries is not None else _env_int("IMAGE_MAX_RETRIES", 2)
        )

    @classmethod
    def _default_model(cls) -> str:
        return "default"

    @classmethod
    def env_config(cls) -> dict:
        """Configuración leída del entorno para instanciar el provider."""
        return {
            "model": _env("IMAGE_MODEL"),
            "timeout": _env_int("IMAGE_TIMEOUT", 120),
            "max_retries": _env_int("IMAGE_MAX_RETRIES", 2),
        }

    @staticmethod
    def _resolve_dimensions(
        width: Optional[int],
        height: Optional[int],
        aspect_ratio: Optional[str],
    ) -> tuple[int, int, str]:
        """Resuelve dimensiones válidas y un aspect ratio coherente."""
        if aspect_ratio and aspect_ratio in VALID_ASPECT_RATIOS:
            w, h = _env_int_value(aspect_ratio)
            if width is None or height is None:
                return w, h, aspect_ratio
            return int(width), int(height), aspect_ratio

        if width and height:
            ratio = _aspect_from_size(int(width), int(height))
            if ratio in VALID_ASPECT_RATIOS:
                return int(width), int(height), ratio
        return 1024, 576, "16:9"

    def generate(
        self,
        prompt: str,
        *,
        negative_prompt: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
        model: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        **kwargs: Any,
    ) -> ImageResult:
        """Genera una imagen completa con reintentos (ver clase)."""
        resolved_model = model or self.model
        if not resolved_model:
            raise ImageProviderError(
                f"El proveedor '{self.name}' no tiene modelo configurado. "
                "Define IMAGE_MODEL o el nombre específico del provider."
            )

        w, h, aspect = self._resolve_dimensions(width, height, aspect_ratio)
        actual_steps = int(steps) if steps is not None else _env_int("IMAGE_STEPS", 20)
        actual_seed = (
            int(seed) if seed is not None else _env_int("IMAGE_SEED", _random_seed())
        )
        actual_prompt = (prompt or "").strip()
        if not actual_prompt:
            raise ImageInvalidResponseError("El prompt no puede estar vacío.")

        last_error: Optional[ImageProviderError] = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._generate_once(
                    prompt=actual_prompt,
                    negative_prompt=negative_prompt or "",
                    width=w,
                    height=h,
                    steps=actual_steps,
                    seed=actual_seed,
                    model=resolved_model,
                    aspect_ratio=aspect,
                    **kwargs,
                )
            except (ImageTimeoutError, ImageConnectionError) as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(_delay_backoff(attempt))
                else:
                    raise last_error  # type: ignore[misc]
        raise last_error  # type: ignore[misc]

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
        """A implementar por cada proveedor. Retorna ImageResult."""
        raise NotImplementedError

    def health_check(self) -> dict:
        """Comprueba la salud del proveedor (debe devolver un dict)."""
        raise NotImplementedError

    def available_models(self) -> list:
        """Devuelve la lista de modelos disponibles (o [] si no aplica)."""
        return []




    def get_metadata(self) -> dict:
        """Metadatos descriptivos del proveedor y su configuración actual.

        Incluye los datos comunes (provider, model, timeout, retries, steps)
        y extiende con ``_metadata_extras`` para los metadatos propios de un
        provider concreto.
        """
        metadata: dict[str, Any] = {
            "provider": self.name,
            "model": self.model,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "steps": _env_int("IMAGE_STEPS", 20),
            "default_model": self._default_model(),
        }
        metadata.update(self._metadata_extras() or {})
        return metadata

    def _metadata_extras(self) -> dict:
        """Hook opcional para que los providers aporten metadatos extra."""
        return {}


# ----------------------------------------------------------------------------
# Helpers internos
# ----------------------------------------------------------------------------
_DEFAULT_SIZES = {
    "16:9": (1024, 576),
    "3:2": (1280, 864),
    "4:3": (1024, 768),
    "1:1": (1024, 1024),
    "2:3": (768, 1152),
    "9:16": (576, 1024),
}


def _env_int_value(aspect_ratio: str) -> tuple[int, int]:
    """Devuelve un tamaño base (w, h) para un aspect ratio admitido."""
    return _DEFAULT_SIZES.get(aspect_ratio, (1024, 576))


def _aspect_from_size(width: int, height: int) -> str:
    """Calcula el aspect ratio de un tamaño, al admitido mas proximo."""
    from fractions import Fraction

    ratio = Fraction(int(width), int(height))
    w, h = ratio.numerator, ratio.denominator
    for ar in VALID_ASPECT_RATIOS:
        aw, ah = ar.split(":")
        if abs(w / h - int(aw) / int(ah)) < 0.05:
            return ar
    return ""


def _random_seed() -> int:
    """Semilla pseudoaleatoria estable (sobreescribible en tests)."""
    return int.from_bytes(hashlib.sha256(os.urandom(8)).digest()[:4], "big")
