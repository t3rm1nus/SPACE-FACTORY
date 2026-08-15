"""Proveedor Ollama (local, sin API key).

Soporta el API nativa de Ollama (/api/generate) y es el proveedor por defecto,
de modo que Space Lair puede funcionar sin ninguna API comercial.

Configuración (variables de entorno):
    LLM_PROVIDER=ollama
    OLLAMA_BASE_URL=http://localhost:11434   (o legacy OLLAMA_URL=.../api/generate)
    OLLAMA_MODEL=<modelo>                     (por defecto: llama3.1)
"""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any, Iterator, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from core.providers.base import (
    LLMInvalidResponseError,
    LLMProvider,
    LLMResult,
    _env,
    http_json,
    map_urlerror,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen-agent:latest"


def resolve_ollama_base_url() -> str:
    """Resuelve la URL base de Ollama.

    Lee OLLAMA_BASE_URL (preferido). Si no, usa la variable legacy OLLAMA_URL
    (que normalmente trae la ruta /api/generate) y le quita el sufijo.
    """
    base = _env("OLLAMA_BASE_URL")
    if base:
        return base.rstrip("/")

    legacy = _env("OLLAMA_URL")
    if legacy:
        return legacy.replace("/api/generate", "").rstrip("/")

    return DEFAULT_BASE_URL


class OllamaProvider(LLMProvider):
    """Proveedor LLM que habla con el API nativo de Ollama."""

    name = "ollama"

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        super().__init__(model=model, timeout=timeout, max_retries=max_retries)
        self.base_url = (base_url or resolve_ollama_base_url()).rstrip("/")
        if self.model is None:
            self.model = _env("OLLAMA_MODEL", DEFAULT_MODEL)

    @classmethod
    def env_config(cls) -> dict:
        return {
            "base_url": resolve_ollama_base_url(),
            "model": _env("OLLAMA_MODEL"),
        }

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
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        options: dict[str, Any] = {}
        if max_tokens:
            options["num_predict"] = int(max_tokens)
        if temperature is not None:
            options["temperature"] = float(temperature)
        if options:
            payload["options"] = options
        context = kwargs.pop("context", None)
        if context:
            payload["context"] = context

        raw = http_json(
            "POST",
            f"{self.base_url}/api/generate",
            payload=payload,
            timeout=self.timeout,
        )
        return self._to_result(raw, model)

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
        resolved = model or self.model
        if not resolved:
            raise LLMInvalidResponseError("Modelo no configurado para Ollama")

        payload: dict[str, Any] = {"model": resolved, "prompt": prompt, "stream": True}
        if system:
            payload["system"] = system
        options: dict[str, Any] = {}
        if max_tokens:
            options["num_predict"] = int(max_tokens)
        if temperature is not None:
            options["temperature"] = float(temperature)
        if options:
            payload["options"] = options

        req = Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urlopen(req, timeout=self.timeout)
        except (HTTPError, URLError, socket.timeout, TimeoutError) as e:
            raise map_urlerror(e, provider=self.name) from e

        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("done"):
                    break
                piece = obj.get("response")
                if piece:
                    yield piece
        finally:
            resp.close()

    def _to_result(self, raw: dict, model: str) -> LLMResult:
        if raw.get("error"):
            raise LLMInvalidResponseError(str(raw["error"]))
        text = raw.get("response")
        if not isinstance(text, str) or not text:
            raise LLMInvalidResponseError(
                "Ollama no devolvió 'response' con texto en su respuesta"
            )
        input_tokens = int(raw.get("prompt_eval_count") or 0)
        output_tokens = int(raw.get("eval_count") or 0)
        return LLMResult(
            text=text,
            provider=self.name,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=self._cost(self.name, model, input_tokens, output_tokens),
            raw_response=raw,
            metadata={"url": self.base_url},
        )

    def health_check(self) -> dict:
        checks: dict[str, Any] = {}
        reachable = False
        model_names: list[str] = []
        try:
            tags = http_json("GET", f"{self.base_url}/api/tags", timeout=5)
            reachable = True
            model_names = [
                (m.get("name") or "") for m in (tags.get("models") or [])
            ]
            checks["models"] = model_names
        except Exception as e:
            checks["server"] = str(e)

        healthy = reachable
        if self.model and model_names:
            short = self.model.split(":")[0]
            checks["requested_model_present"] = any(
                (n or "").split(":")[0] == short for n in model_names
            )
        return {
            "healthy": healthy,
            "provider": self.name,
            "model": self.model,
            "checks": checks,
            "status": "🟢 healthy (Ollama local)" if healthy else "🔴 unhealthy (Ollama no accesible)",
        }

    def available_models(self) -> list:
        try:
            tags = http_json("GET", f"{self.base_url}/api/tags", timeout=5)
            return [(m.get("name") or "") for m in (tags.get("models") or [])]
        except Exception:
            return []
