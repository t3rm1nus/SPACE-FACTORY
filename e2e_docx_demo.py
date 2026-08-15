"""Demo end-to-end DEL PIPELINE REAL (sin LLM, fallbacks deterministas).

Valida que `document_builder` genera un DOCX real con python-docx integrado con
el resto del pipeline: write_chapter_es/en -> fact_check -> edit -> translate
-> image plan -> generate images (provider LOCAL png zlib) -> build_book_docx
-> build_book_pdf -> final_quality_control
"""
from __future__ import annotations
import os, sys
from unittest.mock import patch
from docx import Document as DocxDocument


def _no_llm(*a, **k):
    raise RuntimeError("E2E: no LLM (fallback determinista)")


_PATCHED = [
    "modules.book_planner.main.get_provider",
    "modules.chapter_writer.main.get_provider",
    "modules.fact_checker.main.get_provider",
    "modules.editor.main.get_provider",
    "modules.translator.main.get_provider",
    "modules.image_planner.main.get_provider",
]
from core.book.book_schema import Book  # noqa: E402

BOOK = {
    "book_id": 1, "title": "Exploradores del Cosmos",
    "subtitle": "Una introduccion a la astrofisica",
    "description": "Este libro recorre los fundamentos de la astrofisica moderna.",
    "author": "Space Lair", "target_audience": "lectores curiosos",
    "genre": "Ciencia divulgacion", "languages": ["es"], "target_chapters": 3,
    "status": "edited", "created_at": "2024-01-01T00:00:00",
}
PLAN = [
    {"number": 1, "title": "Del punto de partida", "objective": "Presentar el universo observable",
     "sections": [{"heading": "Universo observable", "objective": "Definir horizonte y edad"}],
     "image_requirements": 3},
    {"number": 2, "title": "Estrellas y galaxias", "objective": "Clasificar galaxias y ciclos estelares",
     "sections": [{"heading": "Galaxias", "objective": "Tipos Hubble"},
                  {"heading": "Ciclo estelar", "objective": "Nebulosas"}],
     "image_requirements": 3},
    {"number": 3, "title": "Fuerzas del cosmos", "objective": "Gravedad, expansion y materia oscura",
     "sections": [{"heading": "Gravedad", "objective": "Relatividad"},
                  {"heading": "Materia oscura", "objective": "Evidencia observacional"}],
     "image_requirements": 3},
]
RESEARCH = "El universo observable tiene ~93 mil millones de anios luz de diametro."
SOURCES = [{"url": "https://example.org/universe", "title": "Universo observable"}]


def _read_md(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main():
    os.makedirs("output", exist_ok=True)
    os.makedirs("data/images/local", exist_ok=True)
    patches = [patch(p, _no_llm, create=True) for p in _PATCHED]
    for p in patches:
        p.start()
    try:
        from modules.chapter_writer.main import execute as write_exec
        from modules.fact_checker.main import execute as fact_exec
        from modules.editor.main import execute as edit_exec
        from modules.translator.main import execute as trans_exec
        from modules.image_planner.main import execute as iplan_exec
        from modules.image_generator.main import execute as igen_exec
        from modules.document_builder.main import build_book_docx
        from modules.pdf_builder.main import build_book_pdf
        from modules.quality_control.main import execute as qc_exec

        chapters = []
        prev = []
        for out in PLAN:
            n = out["number"]
            payload = {"book_metadata": dict(BOOK), "chapter_outline": out,
                       "research": RESEARCH, "sources": SOURCES,
                       "previous_chapter_summaries": prev, "target_word_count": 800}
            es = write_exec(payload, capability="write_chapter_es")
            en = write_exec(dict(payload), capability="write_chapter_en")
            draft_es = es["metadata"].get("chapter_md") or _read_md(es["chapter_md_path"])
            draft_en = en["metadata"].get("chapter_md") or _read_md(en["chapter_md_path"])
            fact = fact_exec({"chapter_text": draft_es, "sources": SOURCES,
                              "target_language": "es"}, capability="fact_check_chapter")
            edt = edit_exec({"chapter_text": draft_es,
                             "protected_terms": ["Space Lair"], "facts": [],
                             "references": [], "target_language": "es"},
                            capability="edit_chapter")
            tr = trans_exec({"source_text": edt["edited_text"],
                             "protected_terms": ["Space Lair"]},
                            capability="translate_es_en")
            plan = iplan_exec({"chapter_text": draft_es, "chapter_title": out["title"],
                               "visual_style": "fotografia editorial",
                               "num_images": out["image_requirements"],
                               "language": "es"}, capability="create_chapter_image_plan")
            img = igen_exec({"image_plan": plan, "book_id": n, "chapter_number": n,
                             "language": "es", "skip_existing": True},
                            capability="generate_chapter_images")
            imgs = [r["image_path"] for r in img["results"]
                    if r.get("status") == "ok" and os.path.isfile(r.get("image_path", ""))]
            chapters.append({"chapter_id": 100 + n, "book_id": 1, "number": n,
                             "title": out["title"], "objective": out["objective"],
                             "draft_es": draft_es, "draft_en": draft_en,
                             "edited_es": edt["edited_text"], "edited_en": tr["translated_text"],
                             "images": imgs, "sources": [s["url"] for s in SOURCES],
                             "quality_status": fact["status"], "research": RESEARCH})
            prev.append(draft_es[:200])
            print(f"[capitulo {n}] es={len(draft_es.split())}w en={len(draft_en.split())}w "
                  f"imgs={len(imgs)} fact={fact['status']}")
        book_dict = dict(BOOK); book_dict["chapters"] = chapters
        Book.model_validate(book_dict)
        docx = build_book_docx({"book": book_dict, "language": "es", "page_config": {"size": "A4"}})
        pdf = build_book_pdf({"book": book_dict, "language": "es"})
        qc = qc_exec({"book": book_dict, "docx_path": docx["docx_path"],
                      "pdf_path": pdf["pdf_path"], "min_chapters": 2,
                      "target_chapters": 3, "max_chapters": 5},
                     capability="final_quality_control")
        d = DocxDocument(docx["docx_path"])
        print("\n=== RESULTADOS E2E ===")
        print("docx exists:", os.path.isfile(docx["docx_path"]))
        print("pdf exists :", os.path.isfile(pdf["pdf_path"]))
        print("docx title/author/lang:", d.core_properties.title, "/",
              d.core_properties.author, "/", d.core_properties.language)
        print("inline_shapes:", len(d.inline_shapes), "chapter_count:", docx["chapter_count"],
              "image_count:", docx["image_count"])
        print("pdf chapter_count:", pdf["chapter_count"])
        print("QC overall_status:", qc["overall_status"], "is_complete:", qc["is_complete"])
        print("QC book_checks:", [c["status"] for c in qc["book_checks"]])
        print("QC document_checks:", [c["status"] for c in qc["document_checks"]])
        print("QC chapter_checks:", [c["status"] for c in qc["chapter_checks"]])
        print("QC image_checks:", [c["status"] for c in qc["image_checks"]])
        for key in ("chapter_checks", "image_checks", "language_checks", "source_checks"):
            for c in qc[key]:
                if c["status"] != "PASS":
                    print("  NON-PASS:", c["status"], "-", str(c["message"])[:120])
        ok = (os.path.isfile(docx["docx_path"]) and os.path.isfile(pdf["pdf_path"])
              and d.core_properties.title == BOOK["title"]
              and len(d.inline_shapes) == docx["image_count"]
              and qc["overall_status"] in ("PASS", "WARNING"))
        print("\nVERDICT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    finally:
        for p in patches:
            p.stop()


if __name__ == "__main__":
    sys.exit(main())
