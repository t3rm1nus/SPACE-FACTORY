"""E2E EDITORIAL 001 - Reanudacion controlada.

Ejecuta el pipeline editorial REAL con el payload especificado (image_count=0)
y emite el informe final solicitado.

Pipeline: Hermes(Book Planner) -> Research -> Outline -> Chapter Writer
       -> Fact Checker -> Editor -> Quality Gate -> Checkpoint -> Document Builder.

NO genera imagenes. NO traduce. NO crea PDF.
NO modifica codigo de libreria (solo este runner de prueba).

"""

from __future__ import annotations

import json
import os
import traceback
from pathlib import Path
from typing import Any

# Configuracion del modelo EXIGIDA por el usuario
# La configuración del modelo/entorno se aplica ahora en `_configure_environment()`
# desde `main()` (ver abajo), NO a nivel de importación. Si este módulo se importa
# durante la colección de pytest (tests/test_runner_e2e_001.py), un os.environ a
# nivel módulo contaminaba al proceso y forzaba el backstop del Chapter Writer en
# tests ajenos al runner E2E (root cause 7.9D.7; ver diagnostics/suite_failure.log).
# Garantía de mínimo determinista: el Chapter Writer completa el mínimo operativo
# (1500 palabras) vía motor 100% Python, sin depender del comportamiento del LLM.
# CHAP_FORCE_MIN=1 amplía las secciones hasta el mínimo si el LLM+fallback quedan
# por debajo; CHAP_USE_LLM=0 deshabilta el proveedor LLM por completo.
# (movido a _configure_environment en main())

from core.checkpoint import CheckpointManager, Stage
from core.database import init_db
from modules.book_planner.main import execute as plan_execute
from modules.chapter_writer.main import _detect_placeholder as _chapter_detect_placeholder
from modules.chapter_writer.main import execute as chapter_execute
from modules.document_builder.main import build_book_docx
from modules.editor.main import execute as editor_execute
from modules.fact_checker.main import execute as fc_execute
from modules.quality_control.main import execute as qc_execute
from modules.research.main import execute as research_execute


def _configure_environment() -> None:
    """Aplica la configuración EXIGIDA por el modelo/entorno para la ejecución E2E.

    Se invoca desde ``main()`` (no a nivel de importación) para que procesos que
    solo importan este módulo —como la fase de colección de pytest— no hereden
    ``CHAP_FORCE_MIN=1``/``CHAP_USE_LM=0`` y contaminen el Chapter Writer.
    El runner real se ejecuta como subproceso
    (``python run_e2e_001_editorial.py`` -> ``__main__`` -> ``main()``), por lo
    que el entorno está disponible antes de ``run()``.
    """
    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
    os.environ["OLLAMA_MODEL"] = "qwen-agent:latest"
    os.environ["ROUTER_MODEL"] = "qwen-agent:latest"
    # Garantía de mínimo determinista: el Chapter Writer completa el mínimo
    # operativo (1500 palabras) vía motor 100% Python, sin depender del LLM.
    # CHAP_FORCE_MIN=1 amplía las secciones hasta el mínimo si el LLM+fallback
    # quedan por debajo; CHAP_USE_LM=0 deshabilta el proveedor LLM por completo.
    os.environ["CHAP_FORCE_MIN"] = "1"
    os.environ.setdefault("CHAP_USE_LLM", "1")


BOOK_ID = 1001

CONFIG = {
    "title": "El nacimiento de Internet",
    "language": "es",
    "target_chapters": 1,
    "research_required": True,
    "min_sources": 3,
    "minimum_words": 1500,
    "images": 0,
    "translation": False,
    "docx": True,
    "pdf": False,
}

# Payload EXACTAMENTE como indica el usuario (el que provoco el fallo anterior).
# image_count=0 es el override explicito del workflow; el LLM puede sugerir
# imagenes, pero el normalizador debe forzar image_requirements=0.
PLAN_PAYLOAD = {
    "idea": "La historia del nacimiento y evolucion inicial de Internet (ARPANET, ARPA, TCP/IP).",
    "target_chapters": 1,
    "language": "es",
    "target_audience": "General",
    "desired_length": "3000 palabras",
    "style": "Divulgativo",
    "subject_constraints": "Enfoque en ARPANET, ARPA, TCP/IP.",
    "image_count": 0,
}

RESEARCH_QUERIES = [
    "ARPANET historia",
    "ARPANET ARPA origenes",
    "TCP/IP protocolo historia",
    "inicios de Internet primera red",
]

# Cabeceiras en espanol con acento para que coincidan con la salida natural del LLM
# (el Chapter Writer comprueba que el heading esta contenido en el texto).
OUTLINE = {
    "title": CONFIG["title"],
    "number": 1,
    "objective": "Nacimiento y evolucion inicial de Internet",
    "sections": [
        {"heading": "Introducción", "objective": "Contexto y pregunta central"},
        {"heading": "Orígenes: ARPA y ARPANET", "objective": "Proyectos militares y académicos"},
        {"heading": "El nacimiento de ARPANET", "objective": "Primera red de conmutación de paquetes"},
        {"heading": "La llegada de TCP/IP", "objective": "Unificación de protocolos"},
        {"heading": "Hacia la Internet moderna", "objective": "De ARPANET a Internet"},
        {"heading": "Conclusión", "objective": "Impacto y legado"},
    ],
}

PROTECTED_TERMS = ["ARPANET", "ARPA", "TCP/IP", "Vint Cerf", "Bob Kahn"]


def log(msg: str) -> None:
    print(msg, flush=True)


class StageFailed(Exception):
    """Levanta para detener el pipeline en una etapa con fallo controlado."""

    def __init__(self, stage: str, payload: Any, cause: str):
        super().__init__(f"{stage}: {cause}")
        self.stage = stage
        self.payload = payload
        self.cause = cause


def save_checkpoint(book_id: int, stage: str, payload: dict, **kw) -> str:
    m = CheckpointManager()
    r = m.save(book_id, stage, payload, **kw)
    return r["path"]


def _json_safe(value: Any, _path: list[int] | None = None) -> Any:
    if _path is None:
        _path = []

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    if isinstance(value, Path):
        return str(value)

    obj_id = id(value)
    if obj_id in _path:
        return "<circular_reference>"

    _path.append(obj_id)
    try:
        if isinstance(value, dict):
            return {str(k): _json_safe(v, _path) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v, _path) for v in value]
    except Exception:
        return str(value)
    finally:
        _path.pop()

    return str(value)


def _safe_payload(obj: Any) -> Any:
    return _json_safe(obj)


def _safe_book_description(description: str, title: str = "") -> str:
    """Genera una descripción corta y segura para metadatos DOCX."""
    raw = str(description or title or "Libro")
    looks_like_chapter = (
        raw.startswith("#")
        or raw.startswith("##")
        or raw.count("\n") > 1
    )
    text = raw.replace("\n", " ").strip()
    # Colapsar espacios múltiples
    while "  " in text:
        text = text.replace("  ", " ")
    if looks_like_chapter:
        base = str(title or "Libro").strip()
        text = f"Libro sobre {base}."
    # Limitar a 200 caracteres con margen de seguridad
    if len(text) > 200:
        text = text[:197].rstrip() + "..."
    return text


def run() -> dict:
    report: dict = {
        "book_id": BOOK_ID,
        "title": CONFIG["title"],
        "language": CONFIG["language"],
        "image_count_override": 0,
        "status": "running",
        "book_planner_status": "PENDING",
        "plan_provider": None,
        "plan_model": None,
        "image_requirements": [],
        "plan_title": None,
        "research_status": "PENDING",
        "sources_count": 0,
        "sources": [],
        "outline_status": "PENDING",
        "outline_sections": [s["heading"] for s in OUTLINE["sections"]],
        "chapter_status": "PENDING",
        "chapter_word_count": 0,
        "chapter_execution_mode": "PENDING",
        "chapter_placeholder_detected": None,
        "chapter_quality_errors": [],
        "chapter_md_path": None,
        "chapter_generation_status": None,
        "chapter_generation_word_count": 0,
        "fallback_used": False,
        "effective_chapter_word_count": 0,
        "fact_check_status": "PENDING",
        "claims_checked": 0,
        "supported_claims": 0,
        "unsupported_claims": 0,
        "conflicting_claims": 0,
        "editor_status": "PENDING",
        "editor_input_words": 0,
        "editor_output_words": 0,
        "editor_execution_mode": "PENDING",
        "editor_placeholder_detected": None,
        "quality_gate": "PENDING",
        "checkpoint_path": None,
        "artifacts": [],
        "docx_status": "PENDING",
        "docx_path": None,
        "docx_book_id": None,
        "docx_language": None,
        "docx_chapter_count": None,
        "docx_image_count": None,
        "failed_stage": None,
        "traceback": None,
        "payload": None,
        "last_checkpoint": None,
    }

    current_stage = "unknown"
    try:
        init_db()

        # ===================== [1] BOOK PLANNER =====================
        current_stage = "book_planner"
        log("=== [1/8] BOOK PLANNER (payload con image_count=0) ===")
        plan = plan_execute(dict(PLAN_PAYLOAD))
        chapters = plan.get("chapters") or []
        report["plan_provider"] = plan.get("provider")
        report["plan_model"] = plan.get("model")
        report["image_requirements"] = [c.get("image_requirements") for c in chapters]
        report["book_planner_status"] = "PASS" if chapters else "FAIL"
        report["plan_title"] = plan.get("title") or CONFIG["title"]
        log(f"  provider={plan.get('provider')} model={plan.get('model')} chapters={len(chapters)}")
        log(f"  VALIDACION ESPECIAL: image_requirements={report['image_requirements']} (debe ser [0])")
        if not chapters:
            raise StageFailed(
                "book_planner", {"chapters": chapters},
                "plan sin capítulos (book_planner FAIL)",
            )
        cp = save_checkpoint(
            BOOK_ID, Stage.BOOK_PLAN.value,
            {"stage": "plan", "payload": PLAN_PAYLOAD, "plan": plan},
            quality_status="PASS",
        )
        report["artifacts"].append(cp)
        report["checkpoint_path"] = cp
        report["last_checkpoint"] = cp

        # ===================== [2] RESEARCH =====================
        current_stage = "research"
        log("=== [2/8] RESEARCH (busqueda web REAL, Wikipedia) ===")
        all_sources: list[dict] = []
        seen: set[str] = set()
        for q in RESEARCH_QUERIES:
            res = research_execute({
                "query": q, "max_sources": 3, "min_sources": 3,
                "research_required": True,
            })
            before = len(all_sources)
            for s in res.get("sources", []):
                u = s.get("url")
                if u and u not in seen:
                    seen.add(u)
                    all_sources.append(s)
            log(f"  query={q!r} -> status={res.get('status')} nuevas={len(all_sources)-before} mode={res.get('execution_mode')}")
        report["sources_count"] = len(all_sources)
        report["sources"] = [
            {
                "title": s.get("title"),
                "url": s.get("url"),
                "source_type": s.get("source_type"),
                "relevance": s.get("relevance") or "N/A",
                "accessed_at": s.get("accessed_at"),
            }
            for s in all_sources
        ]
        report["research_status"] = "PASS" if len(all_sources) >= CONFIG["min_sources"] else "FAIL"
        cp = save_checkpoint(
            BOOK_ID, Stage.RESEARCH.value,
            {"stage": "research", "sources": all_sources},
            sources_count=len(all_sources), quality_status=report["research_status"],
        )
        report["artifacts"].append(cp)
        report["checkpoint_path"] = cp
        report["last_checkpoint"] = cp
        log(f"  TOTAL sources={len(all_sources)} status={report['research_status']}")
        if len(all_sources) < CONFIG["min_sources"]:
            raise StageFailed(
                "research", {"queries": RESEARCH_QUERIES},
                f"sources({len(all_sources)}) < min_sources({CONFIG['min_sources']})",
            )

        # ===================== [3] OUTLINE =====================
        current_stage = "outline"
        log("=== [3/8] OUTLINE ===")
        report["outline_status"] = "PASS"
        cp = save_checkpoint(
            BOOK_ID, Stage.OUTLINE.value,
            {"stage": "outline", "outline": OUTLINE}, quality_status="PASS",
        )
        report["artifacts"].append(cp)
        report["checkpoint_path"] = cp
        report["last_checkpoint"] = cp
        log(f"  sections={len(OUTLINE['sections'])}")

        # ===================== [4] CHAPTER WRITER =====================
        current_stage = "chapter"
        log("=== [4/8] CHAPTER WRITER (LLM real, target 3000 palabras) ===")
        research_text = "\n".join(
            f"- {s.get('title')}: {s.get('content', '')[:1500]}" for s in all_sources
        )
        report["research_snippet"] = research_text
        chapter = chapter_execute({
            "book_metadata": {"book_id": BOOK_ID, "title": report["plan_title"], "language": "es"},
            "chapter_outline": OUTLINE,
            "research": research_text,
            "sources": all_sources,
            "previous_chapter_summaries": [],
            "target_word_count": 3000,
            "minimum_words": CONFIG["minimum_words"],
            "research_required": True,
            "style_guide": "Divulgativo, riguroso y claro",
        })
        wc = chapter.get("word_count", 0) or 0
        qg = chapter.get("quality_gate", "PASS")
        report["chapter_generation_status"] = qg
        report["chapter_generation_word_count"] = wc
        report["chapter_word_count"] = wc
        report["effective_chapter_word_count"] = wc
        report["chapter_execution_mode"] = chapter.get("execution_mode")
        report["chapter_quality_errors"] = chapter.get("quality_errors", [])
        report["chapter_md_path"] = chapter.get("chapter_md_path")
        ctext = ""
        try:
            with open(chapter["chapter_md_path"], "r", encoding="utf-8") as f:
                ctext = f.read()
        except Exception:
            ctext = str(chapter.get("metadata", {}).get("text", ""))
        report["chapter_text"] = ctext
        report["chapter_placeholder_detected"] = bool(_chapter_detect_placeholder(ctext))
        report["chapter_status"] = "PASS" if qg == "PASS" else "FAIL"
        cp = save_checkpoint(
            BOOK_ID, Stage.DRAFT.value,
            {"stage": "chapter", "result": chapter},
            word_count=wc, quality_status=qg,
            execution_mode=chapter.get("execution_mode"),
        )
        report["artifacts"].append(cp)
        report["checkpoint_path"] = cp
        report["last_checkpoint"] = cp
        log(f"  word_count={wc} mode={chapter.get('execution_mode')} gate={qg} "
            f"placeholder={report['chapter_placeholder_detected']} errors={chapter.get('quality_errors')}")
        if qg != "PASS":
            raise StageFailed(
                "chapter",
                chapter,
                f"quality gate FAIL: {chapter.get('quality_errors')}",
            )

        # ===================== [5] FACT CHECK =====================
        current_stage = "fact_check"
        log("=== [5/8] FACT CHECK (LLM real) ===")
        fc = fc_execute({
            "chapter_text": ctext,
            "sources": all_sources,
            "target_language": "es",
            "research_required": True,
        })
        report["claims_checked"] = fc.get("claims_checked", 0) or 0
        report["supported_claims"] = fc.get("supported_claims", 0) or 0
        report["unsupported_claims"] = fc.get("unsupported_claims", 0) or 0
        report["conflicting_claims"] = fc.get("conflicting_claims", 0) or 0
        fc_gate = fc.get("quality_gate")
        fc_status = fc.get("status", "WARNING")
        report["fact_check_status"] = "PASS" if fc_gate == "PASS" else (
            "WARNING" if fc_status == "WARNING" else "FAIL")
        cp = save_checkpoint(
            BOOK_ID, Stage.FACT_CHECK.value,
            {"stage": "fact_check", "result": fc},
            quality_status=fc_gate, execution_mode=fc.get("execution_mode"),
        )
        report["artifacts"].append(cp)
        report["checkpoint_path"] = cp
        report["last_checkpoint"] = cp
        log(f"  status={fc_status} claims={report['claims_checked']} "
            f"supported={report['supported_claims']} unsupported={report['unsupported_claims']} "
            f"conflicting={report['conflicting_claims']} gate={fc_gate}")
        if fc_gate == "FAIL":
            raise StageFailed("fact_check", fc, "quality gate FAIL")

        # ===================== [6] EDITOR =====================
        current_stage = "editor"
        log("=== [6/8] EDITOR (LLM real) ===")
        ed = editor_execute({
            "chapter_text": ctext,
            "style_guide": "Divulgativo, riguroso y claro",
            "target_language": "es",
            "protected_terms": PROTECTED_TERMS,
            "facts": [s.get("title", "") for s in all_sources],
            "references": [s.get("url", "") for s in all_sources],
        })
        report["editor_status"] = "PASS" if ed.get("quality_gate") == "PASS" else "FAIL"
        report["editor_input_words"] = ed.get("input_words", 0) or 0
        report["editor_output_words"] = ed.get("output_words", 0) or 0
        report["editor_execution_mode"] = ed.get("execution_mode")
        if ed.get("execution_mode") == "fallback":
            report["fallback_used"] = True
        report["editor_placeholder_detected"] = ed.get("placeholder_detected")
        cp = save_checkpoint(
            BOOK_ID, Stage.EDITED.value,
            {"stage": "editor", "result": ed},
            word_count=ed.get("output_words", 0), quality_status=ed.get("quality_gate"),
            execution_mode=ed.get("execution_mode"),
        )
        report["artifacts"].append(cp)
        report["checkpoint_path"] = cp
        report["last_checkpoint"] = cp
        log(f"  in={report['editor_input_words']} out={report['editor_output_words']} "
            f"mode={ed.get('execution_mode')} gate={ed.get('quality_gate')} "
            f"placeholder={ed.get('placeholder_detected')}")
        if ed.get("quality_gate") == "FAIL":
            raise StageFailed("editor", ed, "quality gate FAIL")
        if report["editor_output_words"] < CONFIG["minimum_words"]:
            raise StageFailed(
                "editor",
                ed,
                f"editor output words ({report['editor_output_words']}) < minimum_words ({CONFIG['minimum_words']})",
            )

        # El word count efectivo validado por el QC final debe reflejar el texto
        # EDITADO (edited_es), que es lo que realmente contiene el DOCX, no el
        # texto original del Chapter Writer. Si el Editor redujo el contenido,
        # el QC verifica el word count real de la salida editada.
        report["effective_chapter_word_count"] = (
            ed.get("output_words") or report.get("effective_chapter_word_count", wc)
        )

        # book_dict REAL construido a partir de los datos producidos por el E2E.
        # El texto que inspecciona QC es el texto REAL final del editor (edited_es).
        edited_es = ed.get("edited_text") or ctext or ""
        editor_output_words = len(edited_es.split())
        book_dict = {
            "book_id": BOOK_ID,
            "title": report["plan_title"] or CONFIG["title"],
            "subtitle": None,
            "description": _safe_book_description(
                report.get("plan_title") or "Libro generado por Space Lair",
                report.get("plan_title") or "Libro generado por Space Lair",
            ),
            "author": "Space Lair",
            "target_audience": "General",
            "genre": "tecnologia",
            "languages": [CONFIG["language"]],
            "target_chapters": CONFIG["target_chapters"],
            "status": "edited",
            "created_at": None,
            "sources": [s.get("url") for s in all_sources if s.get("url")],
            "chapters": [
                {
                    "chapter_id": 1,
                    "book_id": BOOK_ID,
                    "number": 1,
                    "title": OUTLINE["title"],
                    "objective": (
                        OUTLINE["sections"][0]["objective"]
                        if OUTLINE.get("sections") else None
                    ),
                    "research": report.get("research_snippet") or None,
                    "sources": [s.get("url") for s in all_sources if s.get("url")],
                    "draft_es": ctext or None,
                    "edited_es": edited_es or None,
                    "edited_en": None,
                    "images": [],
                    "quality_status": fc.get("status"),
                }
            ],
        }

        # ===================== [8] DOCUMENT BUILDER =====================
        # Se genera el DOCX ANTES del QC real para que el gate inspeccione el documento final.
        current_stage = "document_builder"
        log("=== [8/8] DOCUMENT BUILDER (generacion DOCX real) ===")
        docx_result = build_book_docx({
            "book": book_dict,
            "language": CONFIG["language"],
            "page_config": {
                "size": "A4",
                "margins_mm": {"top": 25.4, "bottom": 25.4, "left": 25.4, "right": 25.4},
            },
        })
        docx_path = docx_result["docx_path"]
        if not os.path.isfile(docx_path):
            raise StageFailed("document_builder", docx_result, f"docx no existe: {docx_path}")
        if not docx_path.endswith(".docx"):
            raise StageFailed("document_builder", docx_result, f"extension incorrecta: {docx_path}")
        docx_size = os.path.getsize(docx_path)
        if docx_size <= 0:
            raise StageFailed("document_builder", docx_result, f"docx vacio: {docx_path}")
        try:
            from docx import Document as _DocxDocument
            _DocxDocument(docx_path)
        except Exception as e:
            raise StageFailed("document_builder", docx_result, f"docx no abre con python-docx: {e}")
        report["docx_status"] = "PASS"
        report["docx_path"] = docx_path
        report["docx_book_id"] = docx_result["book_id"]
        report["docx_language"] = docx_result["language"]
        report["docx_chapter_count"] = docx_result["chapter_count"]
        report["docx_image_count"] = docx_result["image_count"]
        report["artifacts"].append(docx_path)
        log(f"  DOCX generado: path={docx_path} size={docx_size} bytes "
            f"book_id={docx_result['book_id']} chapters={docx_result['chapter_count']} "
            f"images={docx_result['image_count']}")

        # ===================== [7] QUALITY GATE + CHECKPOINT =====================
        # Quality Gate REAL: ejecuta QualityControlModule/final_quality_control sobre
        # el libro producido por el E2E. El gate final deriva ÚNICAMENTE de
        # qc["overall_status"] (fuente única de verdad). Los proxies anteriores
        # (effective_chapter_word_count, editor quality_gate, fact-checker,
        # placeholders, fallback) permanecen como diagnóstico, NO sustituyen al
        # final_quality_control real.
        current_stage = "quality_gate"
        log("=== [7/8] QUALITY GATE + CHECKPOINT (final_quality_control REAL) ===")

        # Ejecución del QC real sobre el libro producido.
        qc = qc_execute(
            {
                "book": book_dict,
                "docx_path": docx_path,
                "pdf_path": None,
                "min_chapters": CONFIG["target_chapters"],
                "target_chapters": CONFIG["target_chapters"],
                "max_chapters": CONFIG["target_chapters"],
            },
            capability="final_quality_control",
        )
        qc_status = qc.get("overall_status") or "FAIL"
        # Fuente única de verdad: PASS sólo si el QC real lo devuelve PASS.
        quality_gate = "PASS" if qc_status == "PASS" else "FAIL"
        report["quality_gate"] = quality_gate
        report["qc_overall_status"] = qc_status
        report["qc_book_checks"] = qc.get("book_checks", [])
        report["qc_source_checks"] = qc.get("source_checks", [])
        report["qc_image_checks"] = qc.get("image_checks", [])
        report["editor_output_words"] = editor_output_words

        # Diagnóstico preservado de etapas anteriores; NO sustituye al QC real.
        diagnostic_reasons: list[str] = []
        if report.get("chapter_placeholder_detected"):
            diagnostic_reasons.append("chapter_placeholder")
        if ed.get("placeholder_detected"):
            diagnostic_reasons.append("editor_placeholder")
        if report.get("fallback_used"):
            diagnostic_reasons.append("fallback_chapter")
        # Proyección de findings del QC real (FAIL/WARNING) a quality_errors.
        quality_errors: list[str] = []
        for grp in (
            qc.get("book_checks", []) + qc.get("chapter_checks", [])
            + qc.get("source_checks", []) + qc.get("image_checks", [])
            + qc.get("document_checks", [])
        ):
            items = grp if isinstance(grp, list) else [grp]
            for item in items:
                st = item.get("status") if isinstance(item, dict) else None
                msg = item.get("message") if isinstance(item, dict) else str(item)
                if st in ("FAIL", "WARNING"):
                    quality_errors.append(f"{st}: {msg}")

        qc_payload_base = {
            "book_id": BOOK_ID,
            "title": report["plan_title"],
            "chapter_generation_word_count": report.get("chapter_generation_word_count", 0),
            "effective_chapter_word_count": report.get("effective_chapter_word_count", 0),
            "editor_output_words": editor_output_words,
            "sources_count": len(all_sources),
            "image_requirements": report["image_requirements"],
            "editor_execution_mode": ed.get("execution_mode"),
            "editor_placeholder_detected": ed.get("placeholder_detected"),
            "editor_input_words": ed.get("input_words", 0),
            "fact_check_status": fc_gate,
            "fallback_used": report.get("fallback_used", False),
            "chapter_status": report.get("chapter_status"),
            "chapter_generation_status": report.get("chapter_generation_status"),
            "qc_overall_status": qc_status,
            "quality_errors": quality_errors,
            "book_checks": qc.get("book_checks", []),
        }
        if quality_gate == "FAIL":
            fail_payload = dict(qc_payload_base)
            fail_payload.update({
                "stage": "failed",
                "quality_gate_status": "FAIL",
                "failed_stage": "quality_gate",
                "reasons": diagnostic_reasons,
            })
            save_checkpoint(
                BOOK_ID, Stage.FINAL_QC.value,
                fail_payload,
                quality_status="FAIL",
            )
            raise StageFailed(
                "quality_gate", report,
                f"final quality gate FAIL (qc_overall_status={qc_status}): "
                f"{', '.join(quality_errors) or 'qc overall_status != PASS'}",
            )
        if report.get("fallback_used"):
            # Fallback registrado como advertencia de integridad, pero el QC
            # real es la fuente de verdad. Si el QC pasó, el pipeline se
            # marca completed y se preserva la trazabilidad en el checkpoint.
            report["fallback_warning"] = True
            if diagnostic_reasons:
                report["fallback_reasons"] = diagnostic_reasons

        ok_payload = dict(qc_payload_base)
        ok_payload.update({
            "stage": "completed",
            "quality_gate_status": "PASS",
            "reasons": [],
        })
        cp = save_checkpoint(
            BOOK_ID, Stage.FINAL_QC.value,
            ok_payload,
            word_count=ed.get("output_words", 0), quality_status="PASS",
            sources_count=len(all_sources),
        )
        report["artifacts"].append(cp)
        report["checkpoint_path"] = cp
        report["last_checkpoint"] = cp
        log(f"  FINAL QC: PASS (qc_overall_status={qc_status} "
            f"edited_words={editor_output_words} "
            f"editor_gate={ed.get('quality_gate')} fc_gate={fc_gate})")

        report["status"] = "completed"
        return report

    except StageFailed as sf:
        report["status"] = "error"
        report["failed_stage"] = sf.stage
        report["error"] = sf.cause
        report["payload"] = _safe_payload(sf.payload)
        report["traceback"] = traceback.format_exc()
        return report
    except Exception as e:
        report["status"] = "error"
        report["failed_stage"] = current_stage
        report["error"] = f"{type(e).__name__}: {e}"
        report["payload"] = _safe_payload(PLAN_PAYLOAD if current_stage == "book_planner" else None)
        report["traceback"] = traceback.format_exc()
        return report


def print_report(r: dict) -> None:
    print("\n" + "=" * 60)
    print("INFORME WORKFLOW EDITORIAL PRUEBA 001")
    print("=" * 60)
    print("BOOK ID:")
    print(f"  {r.get('book_id')}")
    print("STATUS:")
    print(f"  {r.get('status')}")
    print()
    print("BOOK PLANNER:")
    print(f"  status: {r.get('book_planner_status')}")
    print(f"  provider: {r.get('plan_provider')}")
    print(f"  model: {r.get('plan_model')}")
    print(f"  image_requirements: {r.get('image_requirements')}")
    print()
    print("RESEARCH:")
    print(f"  status: {r.get('research_status')}")
    print(f"  sources_count: {r.get('sources_count')}")
    print("  sources:")
    for s in r.get("sources", []):
        print(f"    - title: {s.get('title')}")
        print(f"      url: {s.get('url')}")
        print(f"      source_type: {s.get('source_type')}")
        print(f"      relevance: {s.get('relevance')}")
        print(f"      accessed_at: {s.get('accessed_at')}")
    print()
    print("OUTLINE:")
    print(f"  status: {r.get('outline_status')}")
    print(f"  sections: {r.get('outline_sections')}")
    print()
    print("CHAPTER:")
    print(f"  status: {r.get('chapter_status')}")
    print(f"  word_count: {r.get('chapter_word_count')}")
    print(f"  execution_mode: {r.get('chapter_execution_mode')}")
    print(f"  placeholder_detected: {r.get('chapter_placeholder_detected')}")
    print()
    print("FACT CHECK:")
    print(f"  status: {r.get('fact_check_status')}")
    print(f"  claims_checked: {r.get('claims_checked')}")
    print(f"  supported_claims: {r.get('supported_claims')}")
    print(f"  unsupported_claims: {r.get('unsupported_claims')}")
    print(f"  conflicting_claims: {r.get('conflicting_claims')}")
    print()
    print("EDITOR:")
    print(f"  status: {r.get('editor_status')}")
    print(f"  input_words: {r.get('editor_input_words')}")
    print(f"  output_words: {r.get('editor_output_words')}")
    print(f"  execution_mode: {r.get('editor_execution_mode')}")
    print(f"  placeholder_detected: {r.get('editor_placeholder_detected')}")
    print()
    print("QUALITY GATE:")
    print(f"  status: {r.get('quality_gate')}")
    print()
    print("CHECKPOINT:")
    print(f"  path: {r.get('checkpoint_path') or r.get('last_checkpoint')}")
    print()
    print("DOCUMENT BUILDER:")
    print(f"  status: {r.get('docx_status')}")
    print("DOCX:")
    print(f"  status: {r.get('docx_status')}")
    print(f"  path: {r.get('docx_path')}")
    print(f"  book_id: {r.get('docx_book_id')}")
    print(f"  language: {r.get('docx_language')}")
    print(f"  chapter_count: {r.get('docx_chapter_count')}")
    print(f"  image_count: {r.get('docx_image_count')}")
    print()
    print("ARTIFACTS:")
    print("  paths:")
    for a in r.get("artifacts", []):
        print(f"    - {a}")
    print()
    print("FINAL:")
    print(f"  {'COMPLETED' if r.get('status') == 'completed' else 'ERROR'}")

    if r.get("failed_stage"):
        print()
        print("-" * 60)
        print("DETALLE DE FALLA")
        print("-" * 60)
        print(f"FAILED STAGE: {r.get('failed_stage')}")
        print(f"ERROR: {r.get('error')}")
        print("TRACEBACK:")
        print(r.get("traceback") or "(none)")
        payload = r.get("payload")
        print(f"PAYLOAD: {json.dumps(_json_safe(payload), ensure_ascii=False)[:1200]}")
        print(f"LAST CHECKPOINT: {r.get('last_checkpoint') or '(none)'}")


def main() -> None:
    _configure_environment()
    r = run()
    # Volcar reporte JSON (sin el texto completo del capitulo)
    slim = {k: v for k, v in r.items() if k != "chapter_text"}
    try:
        with open("e2e_001_report.json", "w", encoding="utf-8") as f:
            json.dump(_json_safe(slim), f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa
        print(f"WARN: no se pudo escribir e2e_001_report.json: {e}", flush=True)
    print_report(r)
    print("\nJSON report escrito: e2e_001_report.json", flush=True)


if __name__ == "__main__":
    main()