"""Fix 8F.4B — Scheduler debe pasar capability a module["execute"].

Cierra el gap de cobertura que enmascaró el bug de book_26:
``_execute_with_timeout`` llamaba ``module["execute"](payload)`` SIN
pasar ``capability``, dejando ``image_generator.execute`` en el path
por defecto ``generate_image`` (que requiere ``image_plan`` pre-poblado).
``build_payload`` produce ``image_plan: {}``, así que
``_normalize_specs`` devolvía 0 specs → ``requested: 0``.

Este archivo prueba ``_execute_with_timeout`` DIRECTAMENTE (como lo
llama ``_process_task`` en core/scheduler.py:129), SIN wrappers
manuales que ya pasen capability.
"""
from __future__ import annotations

import pytest

from core.scheduler import _execute_with_timeout


@pytest.fixture(autouse=True)
def _isolate_image_fs(tmp_path, monkeypatch):
    """Aísla el filesystem de imágenes (LocalImageProvider offline)."""
    monkeypatch.setenv("IMAGE_PROVIDER", "local")
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path / "images_root"))
    monkeypatch.setenv("IMAGE_LOCAL_OUTPUT_DIR", str(tmp_path / "local_out"))


def test_execute_with_timeout_passes_capability_to_image_generator():
    """Reproducción exacta del bug de book_26.

    Payload con ``num_images=3`` e ``image_plan={}`` (exactamente como lo
    produce ``editorial.build_payload`` para image_gen).
    ANTES del fix: requested=0 (capability no llegaba, se usaba el default
    ``generate_image`` que ignora num_images).
    DESPUÉS del fix: requested=3.
    """
    from modules.image_generator import main as img_main

    payload = {
        "book_id": 1,
        "chapter_number": 1,
        "language": "es",
        "chapter_text": "Texto del capítulo de prueba para la cobertura "
        "del dispatcher de capability del scheduler.",
        "chapter_title": "Introducción",
        "visual_style": "Fotografía editorial, paleta cálida.",
        "num_images": 3,
        "provider": "local",
        "model": "placeholder",
        "generate_thumbnails": True,
        "skip_existing": True,
        "max_attempts": 2,
    }
    module = {
        "manifest": {"id": "image_generator", "config": {"timeout_seconds": 30}},
        "execute": img_main.execute,
    }

    # Ejercer _execute_with_timeout tal cual _process_task lo llama:
    #   _execute_with_timeout(module, validated_payload, timeout, capability=capability)
    result = _execute_with_timeout(
        module, payload, timeout=30, capability="generate_chapter_images"
    )

    assert result["requested"] == 3, (
        f"El capability no llegó a execute(): requested={result['requested']} "
        "(BUG: scheduler no pasa capability a module['execute'])"
    )
    assert result["generated"] >= 1
    assert result["failed"] == 0


# ---------------------------------------------------------------------------
# CASO B — Compatibilidad hacia atrás: módulos legacy sin capability
# ---------------------------------------------------------------------------
def test_execute_with_timeout_backward_compat_payload_only_module():
    """Módulos legacy que NO aceptan capability (book_planner,
    word_counter, mcp_demo) deben seguir funcionando sin romperse.

    ``module["execute"](payload, capability)`` lanza TypeError; el
    fallback a ``module["execute"](payload)`` debe activarse.
    """
    captured = {}

    def legacy_execute(payload: dict) -> dict:
        """Firma antigua: solo (payload)."""
        captured["called_with_payload"] = payload
        return {"ok": True, "echo": payload.get("key")}

    module = {
        "manifest": {"id": "word_counter", "config": {"timeout_seconds": 30}},
        "execute": legacy_execute,
    }
    payload = {"key": "value"}

    result = _execute_with_timeout(
        module, payload, timeout=30, capability="count_words"
    )

    assert result == {"ok": True, "echo": "value"}
    assert captured.get("called_with_payload") == {"key": "value"}


def test_execute_with_timeout_capability_reaches_aware_module():
    """Un módulo que SÍ acepta capability debe recibir el valor correcto,
    verificando regresión (fact_checker, editor, etc. siguen igual).
    """
    captured = {}

    def modern_execute(payload: dict, capability: str = "default") -> dict:
        captured["capability"] = capability
        captured["payload"] = payload
        return {"ok": True}

    module = {
        "manifest": {"id": "fact_checker", "config": {"timeout_seconds": 30}},
        "execute": modern_execute,
    }
    payload = {"chapter_id": 42}

    result = _execute_with_timeout(
        module, payload, timeout=30, capability="fact_check_chapter"
    )

    assert result["ok"] is True
    assert captured["capability"] == "fact_check_chapter"
    assert captured["payload"] == {"chapter_id": 42}


# ---------------------------------------------------------------------------
# CASO C — image_generator con image_plan PRE-POBLADO (capability=generate_image)
# ---------------------------------------------------------------------------
def test_execute_with_timeout_generate_image_with_prebuilt_plan():
    """Si image_plan ya está poblado y capability="generate_image",
    el path legacy (generate_image directo) sigue funcionando.
    Verifica que el fix no rompe el caso donde image_plan no está vacío.
    """
    from modules.image_generator import main as img_main

    spec = {
        "image_id": "img_01_hero",
        "purpose": "Hero de apertura.",
        "description": "Hero del capítulo.",
        "composition": "Amplia.",
        "subject": "Tema central.",
        "environment": "Escenario general.",
        "lighting": "Luz suave.",
        "visual_style": "editorial",
        "aspect_ratio": "16:9",
        "prompt": "Tema central, escenario general, amplio. Estilo visual: editorial.",
        "negative_prompt": "text, watermark, low quality",
        "caption": "Figura 1.",
        "placement": "Apertura.",
    }
    payload = {
        "image_plan": {"images": [spec], "visual_style": "editorial"},
        "book_id": 1,
        "chapter_number": 1,
        "language": "es",
        "provider": "local",
        "model": "placeholder",
        "generate_thumbnails": True,
        "skip_existing": True,
        "max_attempts": 2,
    }
    module = {
        "manifest": {"id": "image_generator", "config": {"timeout_seconds": 30}},
        "execute": img_main.execute,
    }

    result = _execute_with_timeout(
        module, payload, timeout=30, capability="generate_image"
    )

    assert result["requested"] == 1
    assert result["failed"] == 0
