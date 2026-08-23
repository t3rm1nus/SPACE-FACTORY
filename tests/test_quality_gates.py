"""Tests de los quality gates y execution_mode introducidos."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.workflow import _workflow_quality_gate
from modules.chapter_writer.main import _validate_quality
from modules.fact_checker.main import execute as fact_check_execute
from modules.research.main import _is_placeholder, execute as research_execute


def _text_words(n: int) -> str:
    return " ".join(f"palabra{i}" for i in range(n))


# ---------------- RESEARCH ----------------

def test_research_no_sources_required_true_fails(monkeypatch):
    """1. Research con 0 fuentes y research_required=true -> FAIL."""
    from modules import research as resmod

    def fake_research(query, max_sources=8, timeout=20, language="es", topic=None):
        return {
            "query": query, "status": "FAIL", "execution_mode": "real",
            "sources": [], "stored_sources": [], "source_count": 0, "error": "sin red",
        }
    monkeypatch.setattr(resmod.main, "research_web", fake_research)
    out = research_execute({"query": "x", "research_required": True})
    assert out["status"] == "FAIL"
    assert out["quality_gate"] == "FAIL"


def test_research_with_sources_passes(monkeypatch):
    """2. Research con fuentes -> PASS."""
    from modules import research as resmod

    def fake_research(query, max_sources=8, timeout=20, language="es", topic=None):
        return {
            "query": query, "status": "PASS", "execution_mode": "real",
            "sources": [{"url": "https://x"}], "stored_sources": [{"url": "s"}],
            "source_count": 3, "error": None,
        }
    monkeypatch.setattr(resmod.main, "research_web", fake_research)
    out = research_execute({"query": "x", "research_required": True})
    assert out["status"] == "PASS"


def test_research_not_required_zero_sources_passes(monkeypatch):
    """3. research_required=false y 0 fuentes -> PASS."""
    from modules import research as resmod

    def fake_research(query, max_sources=8, timeout=20, language="es", topic=None):
        return {
            "query": query, "status": "FAIL", "execution_mode": "real",
            "sources": [], "stored_sources": [], "source_count": 0, "error": "sin red",
        }
    monkeypatch.setattr(resmod.main, "research_web", fake_research)
    out = research_execute({"query": "x", "research_required": False})
    assert out["status"] == "PASS"


# ---------------- CHAPTER WRITER ----------------

def test_chapter_41_words_fails():
    """4. Chapter de 41 palabras -> FAIL."""
    md = _text_words(41)
    q = _validate_quality(md, {"minimum_words": 1500})
    assert q["quality_gate"] == "FAIL"


def test_chapter_minimum_words_passes():
    """5. Chapter de >=1500 palabras valido -> PASS."""
    md = _text_words(1600)
    q = _validate_quality(md, {"minimum_words": 1500})
    assert q["quality_gate"] == "PASS"


def test_chapter_placeholder_fails():
    """6. Placeholder -> FAIL."""
    q = _validate_quality("Desarrollar el nucleo del capítulo 1.", {"minimum_words": 100})
    assert q["quality_gate"] == "FAIL"


def test_chapter_writer_execute_placeholder_fails(monkeypatch):
    """Execute del chapter writer con placeholder y research_required=true -> FAIL."""
    from modules import chapter_writer as cw

    class FakeResult:
        text = "Desarrollar el nucleo del capítulo 1."
        raw_response = ""
        model = "x"
        input_tokens = 0
        output_tokens = 0

    class FakeProvider:
        name = "ollama"

# ---------------- FACT CHECK ----------------

def test_fact_check_no_sources_required_fails(monkeypatch):
    """7. Fact check sin fuentes cuando son obligatorias -> FAIL."""
    from modules import fact_checker as fc

    class FakeProvider:
        name = "ollama"
        def generate(self, *a, **k):
            r = MagicMock()
            r.text = '{"status":"PASS","claims_checked":1,"issues":[]}'
            r.input_tokens = 0
            r.output_tokens = 0
            r.model = "x"
            return r

    monkeypatch.setattr(fc.main, "get_provider", lambda: FakeProvider())
    out = fact_check_execute({
        "chapter_text": _text_words(300),
        "sources": [],
        "research_required": True,
    })
    assert out["status"] == "FAIL"
    assert out["quality_gate"] == "FAIL"


def test_fact_check_with_sources_passes(monkeypatch):
    """8. Fact check con fuentes -> PASS/WARNING."""
    from modules import fact_checker as fc

    class FakeProvider:
        name = "ollama"
        def generate(self, *a, **k):
            r = MagicMock()
            r.text = '{"status":"PASS","claims_checked":2,"issues":[]}'
            r.input_tokens = 0
            r.output_tokens = 0
            r.model = "x"
            return r

    monkeypatch.setattr(fc.main, "get_provider", lambda: FakeProvider())
    out = fact_check_execute({
        "chapter_text": _text_words(300),
        "sources": [{"url": "u1"}],
        "research_required": True,
    })
    assert out["status"] in ("PASS", "WARNING")
    assert out["quality_gate"] == "PASS"


# ---------------- EDITOR ----------------

def test_editor_placeholder_fails(monkeypatch):
    """9. Editor con placeholder -> FAIL."""
    from modules.editor import main as editor_mod

    class FakeProvider:
        name = "ollama"
        def generate(self, *a, **k):
            r = MagicMock()
            r.text = '{"edited_text":"contenido de prueba"}'
            r.input_tokens = 0
            r.output_tokens = 0
            r.model = "x"
            return r

    monkeypatch.setattr(editor_mod, "get_provider", lambda: FakeProvider())
    out = editor_mod.execute({
        "chapter_text": "contenido de prueba",
        "protected_terms": [],
        "facts": [],
        "references": [],
    })
    assert out["quality_gate"] == "FAIL"
    assert out["placeholder_detected"] is True


# ---------------- FALLBACK ----------------

def test_fallback_marked_as_fallback(monkeypatch):
    """10. Fallback queda marcado como fallback."""
    from modules import chapter_writer as cw

    monkeypatch.setattr(cw.main, "get_provider", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    out = cw.main.execute({
        "book_metadata": {"book_id": 1, "title": "T"},
        "chapter_outline": {"number": 1, "title": "C1", "sections": []},
        "research": "investigación real",
        "sources": [{"url": "u1"}],
        "target_word_count": 3000,
        "research_required": True,
    })
    assert out["execution_mode"] == "fallback"


# ---------------- WORKFLOW ----------------

def test_workflow_critical_fail_blocks_completed():
    """11. Workflow no puede llegar a COMPLETED con una etapa crítica FAIL."""
    steps = [
        {"step_id": "chapter1", "capability": "write_chapter_es", "status": "done",
         "result": {"quality_gate": "FAIL", "quality_errors": ["placeholders"], "status": "PASS"}},
    ]
    ok, errors = _workflow_quality_gate(steps)
    assert ok is False
    assert any("chapter" in e for e in errors)


def test_workflow_quality_gate_passes_with_valid_chapter():
    """12. Workflow con etapas válidas llega a COMPLETED."""
    steps = [
        {"step_id": "plan", "capability": "create_book_plan", "status": "done", "result": {}},
        {"step_id": "chapter1", "capability": "write_chapter_es", "status": "done",
         "result": {"quality_gate": "PASS", "status": "PASS"}},
        {"step_id": "editor", "capability": "edit_chapter", "status": "done",
         "result": {"quality_gate": "PASS", "status": "PASS"}},
    ]
    ok, errors = _workflow_quality_gate(steps)
    assert ok is True
    assert errors == []


def test_placeholder_helper():
    assert _is_placeholder("Desarrollar el nucleo") is True
    assert _is_placeholder("Texto editorial real y completo") is False


def test_checkpoint_metrics(tmp_path):
    """13. Las métricas quedan almacenadas en checkpoints."""
    from core.checkpoint import CheckpointManager
    m = CheckpointManager(base_dir=str(tmp_path / "cp"))
    art = m.save(
        7, "draft", {"text": "contenido"},
        execution_mode="fallback", quality_status="FAIL",
        sources_count=0, word_count=41,
    )
    assert art["execution_mode"] == "fallback"
    assert art["quality_status"] == "FAIL"
    assert art["metrics"]["word_count"] == 41
    assert art["metrics"]["sources_count"] == 0
