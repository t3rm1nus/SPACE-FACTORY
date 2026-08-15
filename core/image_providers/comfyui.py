"""Proveedor de imágenes ComfyUI de Space Lair.

Cliente del API HTTP de `ComfyUI
<https://github.com/comfyanonymous/ComfyUI>`_ (difusión local, GPU opcional).

Flujo (``text-to-image`` clásico):

1. Se construye un *workflow* de nodos (o se acepta uno externo vía ``workflow``).
2. ``_generate_once`` hace ``POST /prompt`` y obtiene un ``prompt_id``.
3. Se *poll*ea ``GET /history/{prompt_id}`` hasta que aparece el output.
4. Se descarga la imagen (``GET /view?filename=...``), se guarda localmente
   y se devuelve un :class:`~core.image_providers.base.ImageResult`.

Configuración (variables de entorno):

    IMAGE_PROVIDER=comfyui
    COMFYUI_URL=http://127.0.0.1:8188
    IMAGE_MODEL=comfyai/sdxl          (nombre lógico del modelo)
    IMAGE_SEED, IMAGE_STEPS, IMAGE_TIMEOUT, IMAGE_MAX_RETRIES
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from core.image_providers.base import (
    ImageProvider,
    ImageProviderError,
    ImageResult,
    http_json,
    _env,
    _env_int,
)
from core.image_providers.local import is_valid_png

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8188"
DEFAULT_OUTPUT_DIR = "data/images/comfyui"
# Workflow mínimo de text-to-image (KSampler + SaveImage) sobreesccribible.
DEFAULT_WORKFLOW: dict[str, Any] = {
    "3": {"class_type": "KSampler", "inputs": {}},
    "4": {"class_type": "VAEDecode", "inputs": {}},
    


class ComfyUiProvider(ImageProvider):
    """Cliente HTTP del API de ComfyUI para generación local de imágenes."""

    name = "comfyui"

    def __init__(
        self,
        model: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        output_dir: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        poll_interval: float = 0.5,
        poll_max_wait: float = 120.0,
        **_: Any,
    ) -> None:
        super().__init__(model=model, timeout=timeout, max_retries=max_retries)
        self.base_url = (base_url or _env("COMFYUI_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.output_dir = output_dir or _env("IMAGE_COMFYUI_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)
        self.poll_interval = float(poll_interval)
        self.poll_max_wait = float(poll_max_wait)

    @classmethod
    def _default_model(cls) -> str:
        return _env("IMAGE_MODEL") or "comfyui"

    @classmethod
    def env_config(cls) -> dict:
        return {
            "model": _env("IMAGE_MODEL"),
            "base_url": _env("COMFYUI_URL"),
            "output_dir": _env("IMAGE_COMFYUI_OUTPUT_DIR"),
            "timeout": _env_int("IMAGE_TIMEOUT", 120),
            "max_retries": _env_int("IMAGE_MAX_RETRIES", 2),
        }

    def _build_workflow(
        self,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        steps: int,
        seed: int,
        model: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Construye el workflow de nodos. Acepta una plantilla custom en kwargs."""
        workflow = kwargs.get("workflow") or _deep_copy(DEFAULT_WORKFLOW)
        save_node = _last_node_by_class(workflow, "SaveImage")
        save_node.setdefault("inputs", {})
        save_node["inputs"]["filename_prefix"] = f"sp_{seed}"
        save_node["inputs"]["prompt"] = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "seed": seed,
            "model": model,
        }
        if kwargs.get("extra_nodes"):
            workflow.update(kwargs.pop("extra_nodes"))
        return workflow

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
        from core.metrics import calculate_cost

        workflow = self._build_workflow(
            prompt, negative_prompt, width, height, steps, seed, model, **kwargs
        )
        submitted = http_json(
            "POST",
            f"{self.base_url}/prompt",
            payload={"prompt": workflow},
            timeout=self.timeout,
        )
        prompt_id = submitted.get("prompt_id") or submitted.get("id")
        if not prompt_id:
            raise ImageProviderError(
                f"ComfyUI no devolvió prompt_id: {submitted!r}"
            )

        history = self._poll_history(prompt_id)
        images = _extract_images(history)
        if not images:
            raise ImageProviderError(
                f"ComfyUI no devolvió imágenes para prompt_id={prompt_id}"
            )

        filename, subfolder, folder_type = images[0]
        base_name = os.path.splitext(filename)[0] or "img"
        out_path = os.path.join(self.output_dir, f"{seed}_{base_name}.png")
        self._download_image(filename, subfolder, folder_type, out_path)

        if not is_valid_png(out_path):
            raise ImageProviderError(
                f"La imagen descargada no es PNG válida: {out_path}"
            )
        try:
            cost = calculate_cost("comfyui", model, 0, 0)
        except Exception:
            cost = 0.0

        return ImageResult(
            image_path=out_path,
            provider=self.name,
            model=model,
            seed=seed,
            cost=float(cost or 0.0),
            raw_response={"prompt_id": prompt_id, "history": history},
            metadata={
                "width": width,
                "height": height,
                "steps": steps,
                "aspect_ratio": aspect_ratio,
                "seed": seed,
                "negative_prompt": negative_prompt,
                "comfyui": {"prompt_id": prompt_id, "base_url": self.base_url},
            },
        )

