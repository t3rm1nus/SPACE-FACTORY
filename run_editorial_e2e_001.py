"""WORKFLOW EDITORIAL REAL - PRUEBA 001

Ejecuta el pipeline editorial usando los modulos reales de Space Lair:
Hermes -> Book Planner -> Research -> Source Manager -> Outline
-> Chapter Writer -> Fact Checker -> Editor -> Quality Gate -> Checkpoint
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

os.environ["LLM_PROVIDER"] = "ollama"
os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
os.environ["OLLAMA_MODEL"] = "qwen-agent:latest"
os.environ["ROUTER_MODEL"] = "qwen-agent:latest"

from core.checkpoint import CheckpointManager, Stage
from core.database import init_db

BOOK_ID = 1001

PROJECT = {
    "title": "El nacimiento de Internet",
    "language": "es",
    "target_chapters": 1,
    "research_required": True,
    "min_sources": 3,
    "minimum_words": 1500,
    "images": 0,
    "translation": False,
    "docx": False,
    "pdf": False,
}

research_queries = [
    "ARPANET historia",
    "ARPANET ARPA origenes",
    "TCP/IP protocolo historia",
    "inicios de Internet primera red",
]


def save_checkpoint(book_id, stage, payload, **kw):
    m = CheckpointManager()
    r = m.save(book_id, stage, payload, **kw)
    return r["path"]


def run():
    report = {
        "book_id": BOOK_ID,
        "status": "running",
        "title": PROJECT["title"],
        "language": PROJECT["language"],
        "research": "PENDING",
        "outline": "PENDING",
        "chapter": "PENDING",
        "fact_check": "PENDING",
        "editor": "PENDING",
        "quality_gate": "PENDING",
        "checkpoint": "PENDING",
        "sources": [],
        "artifacts": [],
        "failed_stage": None,
    }
    try:
        init_db()

        # ---------------- BOOK PLANNER ----------------
        from modules.book_planner.main import execute as plan_execute
        print("[1] BOOK PLANNER")
        plan = plan_execute({
            "idea": "La historia del nacimiento y evolucion inicial de Internet (ARPANET, ARPA, TCP/IP).",
            "target_chapters": 1,
            "language": "es",
            "target_audience": "General",
            "desired_length": "3000 palabras",
            "style": "Divulgativo",
            "subject_constraints": "Enfoque en ARPANET, ARPA, TCP/IP y el paso a Internet moderno.",
        })
        plan_title = plan.get("title") or PROJECT["title"]
        plan_chapters = plan.get("chapters") or []
        report["plan_title"] = plan_title
        cpath = save_checkpoint(BOOK_ID, Stage.OUTLINE.value, {
            "stage": "plan", "plan": plan,
        })
        report["artifacts"].append(cpath)
        print(f"  plan_title={plan_title} chapters={len(plan_chapters)}")

        # ---------------- RESEARCH ----------------
        print("[2] RESEARCH")
        from modules.research.main import execute as research_execute
        all_sources = []
        seen = set()
        for q in research_queries:
            res = research_execute({
                "query": q, "max_sources": 3, "min_sources": 3,
                "research_required": True,
            })
            for s in res.get("sources", []):
                u = s.get("url")
                if u and u not in seen:
                    seen.add(u)
                    all_sources.append(s)
            print(f"  query={q!r} -> status={res['status']} sources={res['source_count']}")

        report["research"] = "PASS" if len(all_sources) >= 3 else "FAIL"
        report["sources"] = [
            {"url": s.get("url"), "title": s.get("title"),
             "source_type": s.get("source_type"), "accessed_at": s.get("accessed_at"),
             "relevance": s.get("relevance")} for s in all_sources
        ]
        cpath = save_checkpoint(BOOK_ID, Stage.RESEARCH.value, {
            "stage": "research", "sources": all_sources,
        }, sources_count=len(all_sources), quality_status=report["research"])
        report["artifacts"].append(cpath)
        print(f"  TOTAL sources={len(all_sources)}")

        if len(all_sources) < PROJECT["min_sources"]:
            report["status"] = "error"
            report["failed_stage"] = "research"
            report["error"] = f"sources({len(all_sources)}) < min_sources({PROJECT['min_sources']})"
            return report


        # ---------------- OUTLINE ----------------
        print("[3] OUTLINE")
        outline = {
            "title": plan_title,
            "number": 1,
            "objective": "Nacimiento y evolucion inicial de Internet",
            "sections": [
                {"heading": "Introduccion", "objective": "Contexto y pregunta central"},
                {"heading": "Los origenes: ARPA y ARPANET", "objective": "Proyectos militares y academicos"},
                {"heading": "El nacimiento de ARPANET", "objective": "Primera red de conmutacion de paquetes"},
                {"heading": "La llegada de TCP/IP", "objective": "Unificacion de protocolos"},
                {"heading": "Hacia la Internet moderna", "objective": "De ARPANET a Internet"},
                {"heading": "Conclusion", "objective": "Impacto y legado"},
            ],
        }
        report["outline"] = "PASS"
        report["outline_sections"] = [s["heading"] for s in outline["sections"]]
        cpath = save_checkpoint(BOOK_ID, Stage.OUTLINE.value, {"stage": "outline", "outline": outline})
        report["artifacts"].append(cpath)

        # ---------------- CHAPTER WRITER ----------------
        print("[4] CHAPTER WRITER (LLM real)")
        from modules.chapter_writer.main import execute as chapter_execute
        research_text = "\n".join(
            f"- {s.get('title')}: {s.get('content', '')[:1500]}" for s in all_sources
        )
        chapter = chapter_execute({
            "book_metadata": {"book_id": BOOK_ID, "title": plan_title, "language": "es"},
            "chapter_outline": outline,
            "research": research_text,
            "sources": all_sources,
            "previous_chapter_summaries": [],
            "target_word_count": 3000,
            "minimum_words": PROJECT["minimum_words"],
            "research_required": True,
            "style_guide": "Divulgativo, riguroso y claro",
        })
        wc = chapter.get("word_count", 0)
        qg = chapter.get("quality_gate", "PASS")
        report["chapter_mode"] = chapter.get("execution_mode")
        report["chapter"] = "PASS" if qg == "PASS" else "FAIL"
        report["chapter_word_count"] = wc
        report["chapter_errors"] = chapter.get("quality_errors", [])
        cpath = save_checkpoint(BOOK_ID, Stage.DRAFT.value, {
            "stage": "chapter", "result": chapter,
        }, word_count=wc, quality_status=qg, execution_mode=chapter.get("execution_mode"))
        report["artifacts"].append(cpath)
        report["chapter_md_path"] = chapter.get("chapter_md_path")
        print(f"  word_count={wc} quality_gate={qg} mode={chapter.get('execution_mode')}")

        if qg != "PASS":
            report["status"] = "error"
            report["failed_stage"] = "chapter"
            report["quality_gate"] = "FAIL"
            report["error"] = f"chapter quality gate FAIL: {chapter.get('quality_errors')}"
            return report

        chapter_text = ""
        try:
            with open(chapter["chapter_md_path"], "r", encoding="utf-8") as f:
                chapter_text = f.read()
        except Exception:
            chapter_text = str(chapter.get("metadata", {}).get("text", ""))


        # ---------------- FACT CHECK ----------------
        print("[5] FACT CHECK")
        from modules.fact_checker.main import execute as fc_execute
        fc = fc_execute({
            "chapter_text": chapter_text,
            "sources": all_sources,
            "target_language": "es",
            "research_required": True,
        })
        report["fact_check"] = "PASS" if fc.get("status") == "PASS" else (
            "WARNING" if fc.get("status") == "WARNING" else "FAIL")
        report["claims_checked"] = fc.get("claims_checked")
        report["supported_claims"] = fc.get("supported_claims")
        report["unsupported_claims"] = fc.get("unsupported_claims")
        report["conflicting_claims"] = fc.get("conflicting_claims")
        cpath = save_checkpoint(BOOK_ID, Stage.FACT_CHECK.value, {
            "stage": "fact_check", "result": fc,
        }, quality_status=fc.get("quality_gate"))
        report["artifacts"].append(cpath)
        print(f"  status={fc.get('status')} claims={fc.get('claims_checked')}")

        if fc.get("quality_gate") == "FAIL":
            report["status"] = "error"
            report["failed_stage"] = "fact_check"
            report["quality_gate"] = "FAIL"
            report["error"] = "fact check quality gate FAIL"
            return report

        # ---------------- EDITOR ----------------
        print("[6] EDITOR")
        from modules.editor.main import execute as editor_execute
        ed = editor_execute({
            "chapter_text": chapter_text,
            "style_guide": "Divulgativo, riguroso y claro",
            "target_language": "es",
            "protected_terms": ["ARPANET", "ARPA", "TCP/IP", "Vinton Cerf", "Bob Kahn"],
            "facts": [s.get("title", "") for s in all_sources],
            "references": [s.get("url") for s in all_sources],
        })
        report["editor"] = "FAIL" if ed.get("quality_gate") == "FAIL" else "PASS"
        report["editor_input_words"] = ed.get("input_words")
        report["editor_output_words"] = ed.get("output_words")
        report["editor_placeholder"] = ed.get("placeholder_detected")
        cpath = save_checkpoint(BOOK_ID, Stage.EDITED.value, {
            "stage": "editor", "result": ed,
        }, word_count=ed.get("output_words"), quality_status=ed.get("quality_gate"),
            execution_mode=ed.get("execution_mode"))
        report["artifacts"].append(cpath)
        print(f"  in={ed.get('input_words')} out={ed.get('output_words')} gate={ed.get('quality_gate')}")

        if ed.get("quality_gate") == "FAIL":
            report["status"] = "error"
            report["failed_stage"] = "editor"
            report["quality_gate"] = "FAIL"
            report["error"] = "editor quality gate FAIL"
            return report

        # ---------------- FINAL QUALITY GATE / CHECKPOINT ----------------
        print("[7] QUALITY GATE + CHECKPOINT")
        final_ok = (
            len(all_sources) >= PROJECT["min_sources"]
            and wc >= PROJECT["minimum_words"]
            and ed.get("quality_gate") == "PASS"
            and fc.get("quality_gate") == "PASS"
        )
        report["quality_gate"] = "PASS" if final_ok else "FAIL"
        report["status"] = "completed" if final_ok else "error"
        if not final_ok:
            report["failed_stage"] = "final_quality_gate"
            report["error"] = "final quality gate FAIL"
        else:
            cpath = save_checkpoint(BOOK_ID, Stage.FINAL_QC.value, {
                "stage": "completed", "book_id": BOOK_ID,
                "title": plan_title, "word_count": ed.get("output_words"),
                "sources": [s.get("url") for s in all_sources],
            }, word_count=ed.get("output_words"), quality_status="PASS",
                sources_count=len(all_sources))
            report["artifacts"].append(cpath)
            report["checkpoint"] = f"PASS ({cpath})"
        return report

    except Exception as e:
        report["status"] = "error"
        report["failed_stage"] = report.get("failed_stage") or "unknown"
        report["error"] = str(e)
        report["traceback"] = traceback.format_exc()
        return report

def _print_report(r):
    print("\n" + "=" * 60)
    print("INFORME WORKFLOW EDITORIAL PRUEBA 001")
    print("=" * 60)
    print(f"BOOK ID: {r.get('book_id')}")
    print(f"STATUS: {r.get('status')}")
    print()
    print("RESEARCH:")
    print(f"  sources_count: {len(r.get('sources', []))}")
    print("  sources:")
    for s in r.get("sources", []):
        print(f"    - {s.get('url')} | {s.get('title')} | {s.get('source_type')} | {s.get('accessed_at')} | rel={s.get('relevance')}")
    print("OUTLINE:")
    print(f"  sections: {r.get('outline_sections', 'PENDING')}")
    print("CHAPTER:")
    print(f"  word_count: {r.get('chapter_word_count', 'PENDING')}")
    print(f"  quality_gate: {r.get('quality_gate', 'PENDING')}")
    print(f"  mode: {r.get('chapter_mode')}")
    print(f"  errors: {r.get('chapter_errors')}")
    print("FACT_CHECK:")
    print(f"  claims_checked: {r.get('claims_checked', 0)}")
    print(f"  supported_claims: {r.get('supported_claims', 0)}")
    print(f"  unsupported_claims: {r.get('unsupported_claims', 0)}")
    print(f"  conflicting_claims: {r.get('conflicting_claims', 0)}")
    print("EDITOR:")
    print(f"  input_words: {r.get('editor_input_words', 0)}")
    print(f"  output_words: {r.get('editor_output_words', 0)}")
    print(f"  placeholder_detected: {r.get('editor_placeholder_detected', False)}")
    print(f"QUALITY_GATE: {r.get('quality_gate')}")
    print(f"CHECKPOINT: {r.get('checkpoint')}")
    print("ARTIFACTS:")
    for a in r.get("artifacts", []):
        print(f"  - {a}")
    if r.get("chapter_md_path"):
        print(f"CHAPTER MD: {r.get('chapter_md_path')}")

    errores = (
        ("RESEARCH", r.get("research") == "FAIL"),
        ("CHAPTER", r.get("chapter") == "FAIL"),
        ("FACT_CHECK", r.get("fact_check") == "FAIL"),
        ("EDITOR", r.get("editor") == "FAIL"),
    )
    for stage, failed in errores:
        if failed:
            print(f"\nFAILED STAGE: {stage}")
    if r.get("error"):
        print(f"\nERROR: {r.get('error')}")
    if r.get("traceback"):
        print("\nTRACEBACK:")
        print(r["traceback"])


if __name__ == "__main__":
    result = run()
    _print_report(result)

