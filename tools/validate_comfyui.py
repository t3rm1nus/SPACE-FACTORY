"""Validación aislada del proveedor ComfyUI (script real, no pytest).

Uso:
    python tools/validate_comfyui.py            # contra COMFYUI_URL real
    COMFYUI_URL=http://127.0.0.1:9999 python tools/validate_comfyui.py  # prueba fallback

Genera una imagen real vía API (no placeholder) y reporta tiempos.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.image_providers.local import is_valid_png
from core.image_providers.registry import get


def main() -> int:
    expectations = ["1:1", "16:9"]
    provider = get("comfyui")
    print("provider:", provider.name)
    print("base_url:", provider.base_url)
    print("ckpts:", provider.checkpoint_base, "|", provider.checkpoint_refiner)
    print("poll:", provider.poll_interval, "/", provider.poll_max_wait)
    print("=" * 60)

    any_real = False
    for ar in expectations:
        t0 = time.monotonic()
        result = provider.generate(
            "a red apple on a wooden table, photorealistic",
            negative_prompt="blurry, low quality, watermark",
            aspect_ratio=ar,
            model=provider.model,
        )
        dt = time.monotonic() - t0
        fb = result.metadata.get("fallback")
        reason = result.metadata.get("fallback_reason")
        path = result.image_path
        valid = os.path.isfile(path) and is_valid_png(path)
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        w = result.metadata.get("width")
        h = result.metadata.get("height")
        is_real = valid and not fb
        any_real = any_real or is_real
        print(f"[aspect={ar}] elapsed={dt:.1f}s fallback={fb} valid_png={valid} "
              f"size={size}B dims={w}x{h}")
        print(f"    image_path={path}")
        print(f"    metadata={result.metadata}")
        if fb:
            print(f"    FALLBACK reason={reason}")
        print("-" * 60)

    print("RESULT:", "REAL-IMAGE-GENERATED" if any_real else "FALLBACK-ONLY")
    return 0 if any_real else 2


if __name__ == "__main__":
    raise SystemExit(main())