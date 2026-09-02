"""Diagnóstico offline del fix json_no_extraible en book_planner (Partes 1-3).

Reconstrucción del script de la sesión anterior (no persistido). NO lanza
autopilot ni genera book_id nuevo: solo llama a execute() con 3 ideas de
temática distinta e instrumenta el resultado (JSON válido completo vs fallback).

Uso:
    python tools/dev/_diag_planner_fix.py
"""
from __future__ import annotations

import json
import os
import sys

# Raíz del repo en sys.path (el script vive en tools/dev, no en la raíz).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Ollama local (mismo proveedor que la sesión anterior / defaults).
os.environ.setdefault("LLM_PROVIDER", "ollama")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")

from modules.book_planner.main import (  # noqa: E402
    _extract_json,
    _planner_max_tokens,
    _strip_json_comments,
    execute,
)

# 3 ideas de temática DISTINTA, con target_chapters distintos para ejercitar
# el escalado proporcional del presupuesto (incluida Magallanes/Antártida).
IDEAS = [
    {
        "tag": "historica_expedicion",
        # Ancla del fallo reportado: target_chapters=10 con 3 sections/cap dio
        # 2000 tokens y truncó a mitad del capítulo 8.
        "idea": (
            "La expedición de Fernando de Magallanes hacia el océano "
            "Pacífico y el descubrimiento del paso por el sur de la "
            "Antártida, contada como crónica histórica de navegación."
        ),
        "target_chapters": 10,
    },
    {
        "tag": "scifi_marte",
        "idea": (
            "Colonización humana de Marte: desafíos técnicos, éticos y "
            "humanos de establecerse en otro planeta."
        ),
        "target_chapters": 8,
    },
    {
        "tag": "autoayuda_habitos",
        "idea": (
            "Hábitos de productividad personal y gestión del tiempo para "
            "profesionales con agenda saturada."
        ),
        "target_chapters": 6,
    },
]

MAX_ATTEMPTS = 3  # reintentos ante no-determinismo del LLM


def _valid_plan(out: dict, target: int) -> tuple[bool, str]:
    """Valida que el plan NO sea fallback y tenga los target capítulos completos."""
    used_fallback = not out.get("model")  # execute deja model="" en fallback
    if used_fallback:
        return False, "FALLBACK detectado (model vacío / LLM no produjo plan)"
    chapters = out.get("chapters") or []
    if len(chapters) != target:
        return False, f"capítulos={len(chapters)} != target={target}"
    titles = [c.get("title", "") for c in chapters]
    if len(set(titles)) != len(titles):
        dup = sorted({t for t in titles if titles.count(t) > 1})
        return False, f"títulos duplicados={dup}"
    for i, c in enumerate(chapters, 1):
        for marker in ("...", "siguen", "continúan igual", "patrón similar"):
            if marker in (c.get("title", "") or "").lower():
                return False, f"cap {i}: abreviatura/placeholder en título: {c.get('title')!r}"
        secs = c.get("sections") or []
        if not secs:
            return False, f"cap {i}: sin sections"
    if " - Parte " in out.get("title", "") and not used_fallback:
        return False, "título genérico de fallback en salida no-fallback"
    return True, "OK"


def main() -> int:
    all_ok = True
    total = {"intentos": 0, "exitos": 0}
    print("=== Presupuesto max_tokens (formula nueva) ===")
    for tc in (1, 5, 6, 8, 10, 20, 60):
        print(f"  tc={tc:<3} -> {_planner_max_tokens(tc)} tokens")
    print()
    print("=== _strip_json_comments / _extract_json (unidad, sin LLM) ===")
    with_comment = (
        '{"title":"T",\n'
        ' // Capítulos 8 a 10 siguen el mismo patrón\n'
        ' "chapters":[{"number":1,"title":"C1"}]}'
    )
    d = _extract_json(with_comment)
    assert d["chapters"][0]["title"] == "C1", d
    with_url = json.dumps({
        "title": "T",
        "description": "ver https://example.com/a y https://example.com/b",
        "chapters": [],
    })
    assert _extract_json(with_url)["description"].count("https://") == 2
    assert _strip_json_comments("// solo comentario\n") == ""
    print("  OK: comentario '//...' eliminado; URLs dentro de string intactas.")
    print()
    print("=== Diagnóstico LLM por idea (execute) ===")
    for idea in IDEAS:
        tag = idea["tag"]
        target = idea["target_chapters"]
        ok_attempts = 0
        details = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            total["intentos"] += 1
            sys.stdout.write(f"  [{tag}] intento {attempt}/{MAX_ATTEMPTS} ... ")
            sys.stdout.flush()
            try:
                payload = {
                    "idea": idea["idea"],
                    "target_chapters": target,
                    "language": "es",
                    "target_audience": "adultos",
                    "desired_length": "medio",
                    "style": "divulgativo",
                    "subject_constraints": "ninguna",
                }
                out = execute(payload)
                ok, msg = _valid_plan(out, target)
                if ok:
                    ok_attempts += 1
                    total["exitos"] += 1
                    titles = [c["title"] for c in out["chapters"]]
                    print(f"JSON COMPLETO ({len(titles)} cap, títulos distintos), tokens_out={out['tokens_output']}")
                    details.append(titles)
                else:
                    print(f"NO OK: {msg}")
            except Exception as e:  # noqa: BLE001
                print(f"EXCEPCIÓN: {type(e).__name__}: {e}")
            if ok_attempts:
                # Prompt/budget/parser ya son deterministas; 1 éxito basta por
                # fase pasada sin gastar más llamadas LLM caras.
                break
        result = "PASS" if ok_attempts else "FAIL"
        if not ok_attempts:
            all_ok = False
        print(f"  -> [{tag}] target={target}: {ok_attempts}/{len(details) or 1} intentos con éxito [{result}]")
        for i, titles in enumerate(details, 1):
            print(f"      intento {i}: {titles[:3]} ... ({len(titles)} capítulos)")

    print()
    print(f"RESUMEN GLOBAL: {total['exitos']}/{total['intentos']} intentos con éxito.")
    print("VEREDICTO:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())