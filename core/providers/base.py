"""Base de la capa de proveedores LLM de Space Lair.

Define la interfaz común LLMProvider, la estructura de respuesta estándar
(LLMResult) y una jerarquía de excepciones, junto con utilidades compartidas
(timeout, reintentos, cálculo de coste reutilizando core.metrics).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Jerarquía de excepciones
# ----------------------------------------------------------------------------
class LLMError(Exception):
    """Error genérico de la capa de proveedores LLM."""


class LLMConnectionError(LLMError):
    """No se pudo conectar al proveedor (refused, red, HTTP >= 500...)."""


class LLMTimeoutError(LLMError):
    """El proveedor no respondió dentro del timeout configurado."""


class LLMInvalidResponseError(LLMError):
    """La respuesta del proveedor no era válida o no contenía texto."""


class LLMModelNotFoundError(LLMError):
    """El modelo solicitado no existe en el proveedor."""


class LLMProviderNotFoundError(LLMError):
    """No hay un proveedor registrado con ese nombre."""


# ----------------------------------------------------------------------------
# Respuesta estándar
# ----------------------------------------------------------------------------
@dataclass
class LLMResult:
    """Respuesta normalizada de cualquier proveedor LLM.

    Campos:
        text: texto generado.
        provider: nombre del proveedor ("ollama", "anthropic", ...).
        model: modelo usado.
        input_tokens / output_tokens: consumo de tokens (para métricas).
        cost: coste estimado en USD (0.0 si es local/desconocido).
        raw_response: respuesta cruda del proveedor (opcional).
        metadata: contexto adicional (opcional).
    """

    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
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


def map_urlerror(e: Exception, provider: str = "unknown") -> LLMError:
    """Mapea excepciones de red/HTTP/urllib a la jerarquía LLM*.

    - 404 / "model not found"        -> LLMModelNotFoundError
    - HTTP >= 500 / red / refused    -> LLMConnectionError
    - timeout                         -> LLMTimeoutError
    - resto (4xx no esperado, parse)  -> LLMInvalidResponseError
    """
    if isinstance(e, HTTPError):
        code = e.code
        body = b""
        try:
            body = e.read(1024)
        except Exception:
            pass
        text = body.decode("utf-8", "replace")
        low = text.lower()
        is_model_missing = code == 404 or ("model" in low and "not found" in low)
        if is_model_missing:
            return LLMModelNotFoundError(
                f"Modelo no encontrado en '{provider}' (HTTP {code}): {text}".strip()
            )
        if code >= 500:
            return LLMConnectionError(f"El proveedor '{provider}' devolvió HTTP {code}")
        return LLMInvalidResponseError(
            f"Respuesta HTTP {code} inesperada de '{provider}': {text}".strip()
        )

    if isinstance(e, URLError):
        reason = getattr(e, "reason", e)
        if isinstance(reason, socket.timeout):
            return LLMTimeoutError(
                f"Tiempo de espera agotado conectando con '{provider}'"
            )
        return LLMConnectionError(
            f"Error de conexión con '{provider}': {reason}"
        )

    if isinstance(e, (socket.timeout, TimeoutError)):
        return LLMTimeoutError(
            f"Tiempo de espera agotado conectando con '{provider}'"
        )

    return LLMError(f"Error de '{provider}': {e}")


def http_json(
    method: str,
    url: str,
    *,
    payload: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 120,
) -> dict:
    """Realiza una petición HTTP (GET/POST) con body JSON y devuelve el JSON.

    Levanta excepciones LLM* mapeadas (conexión, timeout, modelo, inválida).
    """
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = Request(url, data=data, headers=req_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except (HTTPError, URLError, socket.timeout, TimeoutError) as e:
        raise map_urlerror(e, provider="llm")

    try:
        return json.loads(body)
    except (ValueError, TypeError) as e:
        raise LLMInvalidResponseError(f"Respuesta JSON inválida: {body[:300]}") from e



# ----------------------------------------------------------------------------
# Proveedor base (interfaz común)
# ----------------------------------------------------------------------------
class LLMProvider:
    """Interfaz común que deben cumplir todos los proveedores.

    Métodos principales:
        generate(prompt, ...) -> LLMResult
        stream(prompt, ...)   -> Iterator[str]
        health_check()        -> dict
        available_models()    -> list[str]

    El coste se calcula reutilizando core.metrics.calculate_cost (compatibilidad
    con el sistema de métricas existente).
    """

    name: str = "base"

    def __init__(
        self,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        # Si no se pasa timeout/modelo explícitos, se toman de las variables de
        # entorno (LLM_TIMEOUT, LLM_MAX_RETRIES, LLM_MODEL).
        self.model = model or _env("LLM_MODEL")
        self.timeout = float(timeout if timeout is not None else _env_int("LLM_TIMEOUT", 120))
        self.max_retries = int(max_retries if max_retries is not None else _env_int("LLM_MAX_RETRIES", 3))

    # --- configuración desde entorno (por proveedor) ---
    @classmethod
    def env_config(cls) -> dict:
        """Devuelve kwargs de construcción leídos de las variables de entorno."""
        return {}

        # --- interfaz pública ---
    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResult:
        """Genera una respuesta completa con reintentos.

        Los reintentos se aplican solo a errores transitorios (timeout y
        problemas de conexión/red). Un modelo inexistente o una respuesta
        inválida se propagan de inmediato.

        Si ``seed`` se provee, se reenvía al backend para reproducibilidad
        determinista (p. ej. ``options.seed`` de Ollama); si es ``None``, el
        comportamiento no cambia (default actual).
        """
        resolved = model or self.model
        if not resolved:
            raise LLMError(
                f"El proveedor '{self.name}' no tiene modelo configurado. "
                "Define LLM_MODEL o la variable específica del proveedor."
            )

        # §17 #32: seed opcional → reproducibilidad (determinismo fact_checker).
        # Se inyecta como kwarg
        # genérico; cada provider reenvía (Ollama) o ignora (Anthropic) sin
        # cambiar el comportamiento cuando es None.
        if seed is not None:
            kwargs["seed"] = seed

        last_error: Optional[LLMError] = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._generate_once(
                    prompt,
                    system=system,
                    model=resolved,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )
            except (LLMTimeoutError, LLMConnectionError) as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(_delay_backoff(attempt))
                else:
                    raise last_error

        raise last_error  # type: ignore[misc]

    def stream(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Devuelve un iterador de fragmentos de texto.

        Este método base hace de fallback: si el proveedor no implementa
        streaming real, devuelve el texto completo en un único fragmento.
        """
        result = self.generate(
            prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )
        if result.text:
            yield result.text

    def health_check(self) -> dict:
        """Comprueba la salud del proveedor (debe devolver un dict)."""
        raise NotImplementedError

    def available_models(self) -> list:
        """Devuelve la lista de modelos disponibles (o [] si no aplica)."""
        raise NotImplementedError

    # --- a implementar por cada proveedor ---
    def _generate_once(
        self,
        prompt: str,
        *,
        system: Optional[str],
        model: str,
        max_tokens: Optional[int],
        temperature: Optional[float],
        **kwargs: Any,
    ) -> LLMResult:
        raise NotImplementedError

    # --- utilidades compartidas ---
    def _cost(
        self, provider: str, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Calcula el coste reutilizando core.metrics.calculate_cost."""
        try:
            from core.metrics import calculate_cost

            return float(calculate_cost(provider, model, input_tokens, output_tokens) or 0.0)
        except Exception:  # pragma: no cover - nunca debe romper una llamada
            return 0.0

