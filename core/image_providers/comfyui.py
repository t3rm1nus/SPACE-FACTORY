"""Proveedor de imágenes ComfyUI de Space Lair.

Cliente del API HTTP de `ComfyUI
<https://github.com/comfyanonymous/ComfyUI>`_ (difusión local, GPU opcional),
usando un workflow SDXL Base + Refiner en dos pasadas (nodos 4,5,6,7,10,11,
12,15,16,17,19 del JSON exportado original).

Flujo (text-to-image clásico):
1. _build_workflow construye (deep-copy de la plantilla) el workflow por imagen:
   base y refiner comparten el texto del prompt y del negativo.
2. _generate_once hace POST /prompt y obtiene un prompt_id.
3. Se pollea GET /history/{prompt_id} hasta que aparece el output.
4. Se descarga la imagen (GET /view), se guarda localmente y se devuelve ImageResult.
   Si el servidor no responde o el PNG no es válido, se cae a LocalImageProvider
   (placeholder) en vez de romper image_gen (metadata['fallback']=True).

Configuración (variables de entorno): IMAGE_PROVIDER, COMFYUI_URL,
COMFYUI_CKPT_BASE, COMFYUI_CKPT_REFINER, IMAGE_COMFYUI_OUTPUT_DIR,
COMFYUI_POLL_INTERVAL, COMFYUI_POLL_MAX_WAIT, IMAGE_MODEL, IMAGE_STEPS,
IMAGE_TIMEOUT, IMAGE_MAX_RETRIES.
"""

from __future__ import annotations

import copy
import logging
import os
import time
from typing import Any, Optional
from urllib.parse import urlencode

from core.image_providers.base import (
    ImageProvider,
    ImageProviderError,
    ImageResult,
    ImageTimeoutError,
    _env,
    _env_float,
    _env_int,
    http_bytes,
    http_json,
)
from core.image_providers.local import LocalImageProvider, is_valid_png

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8188"
DEFAULT_OUTPUT_DIR = "data/images/comfyui"
DEFAULT_CKPT_BASE = "sd_xl_base_1.0.safetensors"
DEFAULT_CKPT_REFINER = "sd_xl_refiner_1.0.safetensors"

# Dimensiones SDXL (múltiplos de 64) por aspect ratio. 3:2 == 1152x768.
SDXL_SIZES = {
    "16:9": (1024, 576),
    "3:2": (1152, 768),
    "4:3": (1024, 768),
    "1:1": (1024, 1024),
    "2:3": (768, 1152),
    "9:16": (576, 1024),
}
# Workflow SDXL Base + Refiner en dos pasadas, exportado en formato API desde
# ComfyUI. Se deep-copia por llamada y se sobreescribe por imagen.
#
# Pasada BASE (nodo 10): muestrea por completo y deja residuo para el refiner
# (return_with_leftover_noise=enable, add_noise=enable).
# Pasada REFINER (nodo 11): continúa donde acaba la base; add_noise=disable y
# noise_seed=0 tal cual el JSON original (el ruido ya lo puso la base).
DEFAULT_WORKFLOW: dict[str, Any] = {
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": DEFAULT_CKPT_BASE},
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "base good", "clip": ["4", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "base bad", "clip": ["4", 1]},
    },
    "10": {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
            "add_noise": "enable",
            "noise_seed": 42,
            "steps": 25,
            "cfg": 7.5,
            "sampler_name": "euler",
            "scheduler": "normal",
            "start_at_step": 0,
            "end_at_step": 20,
            "return_with_leftover_noise": "enable",
        },
    },
    "12": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": DEFAULT_CKPT_REFINER},
    },
    "15": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "ref good", "clip": ["12", 1]},
    },
    "16": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "ref bad", "clip": ["12", 1]},
    },
    "11": {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model": ["12", 0],
            "positive": ["15", 0],
            "negative": ["16", 0],
            "latent_image": ["10", 0],
            "add_noise": "disable",
            "noise_seed": 0,
            "steps": 25,
            "cfg": 7.5,
            "sampler_name": "euler",
            "scheduler": "normal",
            "start_at_step": 20,
            "end_at_step": 10000,
            "return_with_leftover_noise": "disable",
        },
    },
    "17": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["11", 0], "vae": ["4", 2]},
    },
    "19": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "space_lair", "images": ["17", 0]},
    },
}


class ComfyUiProvider(ImageProvider):
    """Cliente HTTP del API de ComfyUI (workflow SDXL Base + Refiner)."""

    name = "comfyui"

    def __init__(
        self,
        model: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        output_dir: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        checkpoint_base: Optional[str] = None,
        checkpoint_refiner: Optional[str] = None,
        poll_interval: Optional[float] = None,
        poll_max_wait: Optional[float] = None,
        connect_timeout: Optional[float] = None,
        **_: Any,
    ) -> None:
        super().__init__(model=model, timeout=timeout, max_retries=max_retries)
        self.base_url = (base_url or _env("COMFYUI_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.output_dir = (
            output_dir or _env("IMAGE_COMFYUI_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR
        )
        os.makedirs(self.output_dir, exist_ok=True)
        self.checkpoint_base = (
            checkpoint_base or _env("COMFYUI_CKPT_BASE") or DEFAULT_CKPT_BASE
        )
        self.checkpoint_refiner = (
            checkpoint_refiner or _env("COMFYUI_CKPT_REFINER") or DEFAULT_CKPT_REFINER
        )
        self.poll_interval = float(
            poll_interval if poll_interval is not None
            else _env_float("COMFYUI_POLL_INTERVAL", 0.5)
        )
        self.poll_max_wait = float(
            poll_max_wait if poll_max_wait is not None
            else _env_float("COMFYUI_POLL_MAX_WAIT", 300.0)
        )
        self.connect_timeout = float(
            connect_timeout if connect_timeout is not None
            else _env_float("COMFYUI_CONNECT_TIMEOUT", 10.0)
        )

    @classmethod
    def _default_model(cls) -> str:
        return _env("IMAGE_MODEL") or "comfyui/sdxl"

    @classmethod
    def env_config(cls) -> dict:
        return {
            "model": _env("IMAGE_MODEL"),
            "base_url": _env("COMFYUI_URL"),
            "output_dir": _env("IMAGE_COMFYUI_OUTPUT_DIR"),
            "timeout": _env_int("IMAGE_TIMEOUT", 120),
            "max_retries": _env_int("IMAGE_MAX_RETRIES", 2),
            "checkpoint_base": _env("COMFYUI_CKPT_BASE"),
            "checkpoint_refiner": _env("COMFYUI_CKPT_REFINER"),
            "poll_interval": _env_float("COMFYUI_POLL_INTERVAL", 0.5),
            "poll_max_wait": _env_float("COMFYUI_POLL_MAX_WAIT", 300.0),
            "connect_timeout": _env_float("COMFYUI_CONNECT_TIMEOUT", 10.0),
        }

    def _build_workflow(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        steps: int,
        seed: int,
        **_: Any,
    ) -> dict[str, Any]:
        """Construye el workflow SDXL Base+Refiner para una imagen.

        Deep-copia la plantilla y aplica las sustituciones acordadas:
        nodos 6/15 -> prompt, 7/16 -> negative_prompt, 5 -> width/height,
        10 -> noise_seed/steps, 4/12 -> checkpoints (env), 19 -> prefix con seed.
        El nodo 11 (REFINER) conserva add_noise=disable / noise_seed=0 del JSON
        original; solo se ajusta el steps coherente con la base.
        """
        workflow = copy.deepcopy(DEFAULT_WORKFLOW)
        for nid in ("6", "15"):
            workflow[nid]["inputs"]["text"] = prompt
        for nid in ("7", "16"):
            workflow[nid]["inputs"]["text"] = negative_prompt
        workflow["5"]["inputs"]["width"] = width
        workflow["5"]["inputs"]["height"] = height
        # División de pasadas proporcional (por defecto 25 steps -> 20/5).
        split = max(1, int(round(steps * 0.8)))
        workflow["10"]["inputs"].update({
            "noise_seed": seed,
            "steps": steps,
            "start_at_step": 0,
            "end_at_step": split,
            "return_with_leftover_noise": "enable",
        })
        # Refiner continúa exactamente donde acaba la base.
        workflow["11"]["inputs"].update({
            "steps": steps,
            "start_at_step": split,
            "end_at_step": 10000,
            "return_with_leftover_noise": "disable",
        })
        workflow["4"]["inputs"]["ckpt_name"] = self.checkpoint_base
        workflow["12"]["inputs"]["ckpt_name"] = self.checkpoint_refiner
        workflow["19"]["inputs"]["filename_prefix"] = f"space_lair_{seed}"
        return workflow

    def _poll_history(self, prompt_id: str) -> dict:
        """Poll a /history/{id} hasta que haya output o falle/termine el timeout."""
        deadline = time.monotonic() + self.poll_max_wait
        while time.monotonic() < deadline:
            resp = http_json(
                "GET", f"{self.base_url}/history/{prompt_id}", timeout=self.timeout
            )
            entry = (resp or {}).get(prompt_id) or {}
            outputs = entry.get("outputs") or {}
            for _nid, out in outputs.items():
                if out.get("images"):
                    return resp
            status = (entry.get("status") or {}).get("status_str") or ""
            if status in ("error", "failed"):
                raise ImageProviderError(
                    f"ComfyUI prompt {prompt_id} terminó en error (status={status})"
                )
            time.sleep(self.poll_interval)
        raise ImageTimeoutError(
            f"ComfyUI: timeout esperando prompt_id={prompt_id} "
            f"tras {self.poll_max_wait:.0f}s"
        )

    @staticmethod
    def _extract_images(history: dict, prompt_id: str) -> list[tuple[str, str, str]]:
        """Extrae (filename, subfolder, type) del primer output con imágenes."""
        entry = (history or {}).get(prompt_id) or {}
        outputs = entry.get("outputs") or {}
        for _nid, out in outputs.items():
            for img in (out.get("images") or []):
                if img.get("filename"):
                    return [
                        (img["filename"], img.get("subfolder", ""), img.get("type", "output"))
                    ]
        return []

    def _download_image(self, filename: str, subfolder: str, folder_type: str, out_path: str) -> None:
        query = urlencode({
            "filename": filename,
            "subfolder": subfolder,
            "type": folder_type,
        })
        url = f"{self.base_url}/view?{query}"
        data = http_bytes("GET", url, timeout=self.timeout)
        with open(out_path, "wb") as fh:
            fh.write(data)

    def _fallback_local(
        self,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        steps: int,
        seed: int,
        aspect_ratio: str,
        *,
        reason: str,
    ) -> ImageResult:
        """Fallback opción A: delega en LocalImageProvider y marca el motivo."""
        logger.warning("ComfyUiProvider: fallback a LocalImageProvider: %s", reason)
        local = LocalImageProvider()
        res = local._generate_once(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
            seed=seed,
            model=local.model,
            aspect_ratio=aspect_ratio,
        )
        res.metadata["fallback"] = True
        res.metadata["fallback_reason"] = reason
        return res

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
        # Dimensiones efectivas por aspect ratio (múltiplos de 64 para SDXL).
        eff_w, eff_h = SDXL_SIZES.get(aspect_ratio, (width, height))
        neg = negative_prompt or ""

        try:
            workflow = self._build_workflow(
                prompt=prompt, negative_prompt=neg,
                width=eff_w, height=eff_h, steps=steps, seed=seed, **kwargs,
            )
        except ImageProviderError as e:
            return self._fallback_local(
                prompt, neg, eff_w, eff_h, steps, seed, aspect_ratio,
                reason=str(e) or type(e).__name__,
            )

        # Encolado inicial: timeout de conexión CORTO dedicado (COMFYUI_CONNECT_TIMEOUT),
        # distinto del IMAGE_TIMEOUT de las llamadas de poll posterior. Si el servidor no
        # responde dentro de ese horizonte (connection refused / host inalcanzable), se cae
        # a fallback local de inmediato en vez de esperar el timeout largo.
        try:
            submitted = http_json(
                "POST",
                f"{self.base_url}/prompt",
                payload={"prompt": workflow},
                timeout=self.connect_timeout,
            )
        except ImageProviderError as e:
            return self._fallback_local(
                prompt, neg, eff_w, eff_h, steps, seed, aspect_ratio,
                reason=f"comfyui_unreachable: {e}" or "comfyui_unreachable",
            )

        prompt_id = submitted.get("prompt_id") or submitted.get("id")
        if not prompt_id:
            return self._fallback_local(
                prompt, neg, eff_w, eff_h, steps, seed, aspect_ratio,
                reason=f"ComfyUI no devolvió prompt_id: {submitted!r}",
            )

        history: Optional[dict] = None
        out_path = ""
        try:
            history = self._poll_history(prompt_id)
            images = self._extract_images(history, prompt_id)
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
        except ImageProviderError as e:
            return self._fallback_local(
                prompt, neg, eff_w, eff_h, steps, seed, aspect_ratio,
                reason=str(e) or type(e).__name__,
            )

        try:
            from core.metrics import calculate_cost
            cost = calculate_cost("comfyui", model, 0, 0)
        except Exception:  # noqa: BLE001
            cost = 0.0

        return ImageResult(
            image_path=out_path,
            provider=self.name,
            model=model,
            seed=seed,
            cost=float(cost or 0.0),
            raw_response={"prompt_id": prompt_id, "history": history},
            metadata={
                "width": eff_w,
                "height": eff_h,
                "steps": steps,
                "aspect_ratio": aspect_ratio,
                "negative_prompt": neg,
                "comfyui": {"prompt_id": prompt_id, "base_url": self.base_url},
            },
        )

    def health_check(self) -> dict:
        healthy = False
        detail = ""
        try:
            http_json("GET", f"{self.base_url}/system_stats", timeout=self.timeout)
            healthy = True
        except ImageProviderError as e:
            detail = str(e)
        return {
            "provider": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "healthy": healthy,
            "status": "🟢 healthy (comfyui)" if healthy else "🔴 unhealthy",
            "detail": detail,
        }

    def available_models(self) -> list:
        return [self.checkpoint_base, self.checkpoint_refiner]

    def _metadata_extras(self) -> dict:
        return {
            "base_url": self.base_url,
            "checkpoint_base": self.checkpoint_base,
            "checkpoint_refiner": self.checkpoint_refiner,
            "poll_interval": self.poll_interval,
            "poll_max_wait": self.poll_max_wait,
            "output_dir": self.output_dir,
            "available_models": self.available_models(),
        }
