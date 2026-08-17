"""Genera UNA imagen real vía ComfyUI usando el prompt de fallback de book_30/cap.1.

El prompt de fallback es el que produce _build_fallback_plan para el payload que
menciona 'Pong'/'Atari' (test_image_planner::test_fallback_plan_rellena_con_topicos_concretos_del_capitulo).

NO corre el pipeline completo del libro; solo 1 imagen para inspección visual.
NO actualiza PROJECT_MASTER_STATUS.md.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.image_planner.main import _build_fallback_plan  # noqa: E402
from core.image_providers import get as get_provider  # noqa: E402
from core.image_providers.local import is_valid_png  # noqa: E402

# --- book_30 / cap.1 (caso real documentado en el test de image_planner) ---
TITLE = "Historia de los videojuegos del pong al GTA VI"
CHAPTER_TEXT = (
    "Hace algo más de un siglo, un grupo de científicos experimentaba con ondas "
    "de radio. Ese experimento dio lugar al primer videojuego: Pong, un sencillo "
    "juego de ping pong que revolucionó la industria. La compañía Atari popularizó "
    "el arcade con máquinas de la década de 1970. Más tarde llegó PlayStation y Xbox."
)
VISUAL_STYLE = "Fotografía editorial, paleta coherente, detalle realista"


def main() -> int:
    # 1) Construir el plan de fallback (determinista, 100% Python, sin LLM).
    payload = {
        "chapter_title": TITLE,
        "chapter_text": CHAPTER_TEXT,
        "visual_style": VISUAL_STYLE,
        "num_images": 1,  # Solo 1 imagen para inspección visual.
    }
    plan = _build_fallback_plan(payload)
    img = plan["images"][0]

    prompt = img["prompt"]
    negative_prompt = img["negative_prompt"]
    aspect_ratio = img["aspect_ratio"]
    image_id = img["image_id"]

    print("=" * 70)
    print("BOOK_30 / CAP.1 — Prompt de fallback (hero)")
    print("=" * 70)
    print(f"image_id      : {image_id}")
    print(f"aspect_ratio  : {aspect_ratio}")
    print(f"prompt        : {prompt}")
    print(f"negative_prompt: {negative_prompt}")
    print(f"topics_present: {'Pong' in prompt or 'Atari' in prompt}")
    print(f"title_literal_in_prompt: "
          f"{'historia de los videojuegos' in prompt.lower()}")
    print("=" * 70)

    # 2) Proveedor ComfyUI (default por registry).
    provider = get_provider("comfyui")
    print(f"provider  : {provider.name}")
    print(f"base_url  : {provider.base_url}")
    print(f"ckpt_base : {provider.checkpoint_base}")
    print(f"ckpt_ref  : {provider.checkpoint_refiner}")
    print(f"poll      : {provider.poll_interval} / {provider.poll_max_wait}")
    print("=" * 70)

    # 3) Generar imagen REAL (seed fijo para reproducibilidad).
    seed = int(os.environ.get("IMAGE_SEED", "3001"))
    steps = int(os.environ.get("IMAGE_STEPS", "20"))
    t0 = time.monotonic()
    result = provider.generate(
        prompt=prompt,
        negative_prompt=negative_prompt,
        aspect_ratio=aspect_ratio,
        seed=seed,
        steps=steps,
        model=provider.model,
    )
    elapsed = time.monotonic() - t0

    path = result.image_path
    fb = result.metadata.get("fallback", False)
    valid = os.path.isfile(path) and is_valid_png(path)
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    w = result.metadata.get("width")
    h = result.metadata.get("height")

    print("=" * 70)
    print("RESULTADO GENERACIÓN")
    print("=" * 70)
    print(f"elapsed        : {elapsed:.2f}s")
    print(f"fallback       : {fb}")
    print(f"valid_png      : {valid}")
    print(f"size           : {size} bytes")
    print(f"dimensions     : {w}x{h}")
    print(f"seed           : {result.seed}")
    print(f"prompt_id      : {result.raw_response.get('prompt_id') if result.raw_response else 'N/A'}")
    print(f"image_path     : {os.path.abspath(path)}")
    print("=" * 70)

    return 0 if (valid and not fb) else (2 if fb else 1)


if __name__ == "__main__":
    raise SystemExit(main())
