"""§17 #36 Fase 3 — reset_from_phase(): retry desde la fase de origen real.

Cubre la cascada de dependencias (PHASE_RESET_CASCADE), el reset acotado a
UN capítulo (subs per-chapter + status de fase), los rechazos por seguridad
(job activo, fase global con chapter_number, capítulo inexistente), el alias
"book_planner"→"planner" y el caso sin cascada (docx).

Jobs sintéticos en memoria (sin BD ni store): reset_from_phase muta el dict y
NO persiste (patrón retry_job), así que no se necesita BookJobStore real.
"""

from __future__ import annotations

import pytest

from core import autopilot


def _make_job(status="FAILED", with_writer_en=False):
    """Job sintético: 10 fases del pipeline con subs per-chapter (cap 1-3).

    Los chapter_id de los subs son "101"/"102"/"103" (id BD = 100+number) para
    ejercitar la resolución real number→id; la BD no existe en estos tests y
    el fallback de reset_from_phase usa el number como id solo si la consulta
    falla... aquí monkeypatteamos get_chapters para el mapeo 100+n.
    """
    phases = []
    for p in autopilot.AUTOPILOT_PHASES:
        ph = {
            "id": p["id"],
            "label": p["label"],
            "capability": p["capability"],
            "status": "PASS",
            "started_at": "t0",
            "completed_at": "t1",
            "duration": 1.0,
            "attempts": 1,
            "metrics": {"x": 1},
            "module": "m",
            "task_id": "t",
            "error": None,
        }
        if p["id"] in autopilot.PER_CHAPTER_PHASES or p["id"] == "writer_en":
            ph["subs"] = {
                "done": 3,
                "total": 3,
                "chapters": {
                    str(100 + n): {"status": "PASS", "attempts": 1, "error": None}
                    for n in (1, 2, 3)
                },
            }
        phases.append(ph)
    if with_writer_en:
        phases.append({
            "id": "writer_en", "label": "WRITER EN",
            "capability": "write_chapter_en", "status": "PASS",
            "started_at": None, "completed_at": None, "duration": None,
            "attempts": 0, "metrics": {}, "module": None, "task_id": None,
            "error": None,
            "subs": {"done": 3, "total": 3, "chapters": {
                str(100 + n): {"status": "PASS", "attempts": 1, "error": None}
                for n in (1, 2, 3)}},
        })
    phases[-2]["status"] = "FAIL"  # quality_gate FAIL (caso de uso real)
    phases[-2]["error"] = "quality_gate#overall_status=FAIL"
    phases[-1]["status"] = "PENDING"  # docx nunca llegó
    return {
        "job_id": "book_999", "book_id": 999, "status": status,
        "current_phase": "quality_gate", "phases": phases,
        "docx_path": None, "error": phases[-2]["error"],
        "data": {}, "created_at": "t0", "updated_at": "t0",
    }


@pytest.fixture(autouse=True)
def _fake_chapters(monkeypatch):
    """Mapeo number->chapter_id sin BD: chapter n -> id 100+n."""

    def fake_get_chapters(book_id):
        return [{"id": 100 + n, "number": n} for n in (1, 2, 3)]

    from frontend import editorial

    monkeypatch.setattr(editorial, "get_chapters", fake_get_chapters)


def _phases_by_id(job):
    return {p["id"]: p for p in job["phases"]}


def test_reset_from_phase_image_gen_single_chapter():
    job = _make_job()
    out = autopilot.reset_from_phase(job, "image_gen", chapter_number=2)
    assert out["status"] == "PENDING" and out["error"] is None
    by_id = _phases_by_id(out)
    # image_gen: sub del cap 2 (id 102) PENDING; caps 1 y 3 siguen PASS;
    # la fase pasa a PENDING para que run_job la re-ejecute.
    ig = by_id["image_gen"]
    assert ig["status"] == "PENDING" and ig["attempts"] == 0
    assert ig["subs"]["chapters"]["102"]["status"] == "PENDING"
    assert ig["subs"]["chapters"]["101"]["status"] == "PASS"
    assert ig["subs"]["chapters"]["103"]["status"] == "PASS"
    assert ig["subs"]["done"] == 2
    # Cascada: quality_gate y docx reseteados.
    assert by_id["quality_gate"]["status"] == "PENDING"
    assert by_id["docx"]["status"] == "PENDING"
    # Intactas: fases previas a image_gen.
    for pid in ("planner", "research", "outline", "writer", "fact_check",
                "editor", "image_plan"):
        assert by_id[pid]["status"] == "PASS", pid


def test_reset_from_phase_research_rejects_chapter_number():
    job = _make_job()
    with pytest.raises(ValueError, match="es global"):
        autopilot.reset_from_phase(job, "research", chapter_number=1)
    # Sin efectos colaterales tras el rechazo.
    assert job["status"] == "FAILED"

def test_reset_from_phase_writer_full_book():
    job = _make_job()
    out = autopilot.reset_from_phase(job, "writer", chapter_number=None)
    by_id = _phases_by_id(out)
    for pid in ("writer", "fact_check", "editor", "image_plan", "image_gen",
                "quality_gate", "docx"):
        assert by_id[pid]["status"] == "PENDING", pid
    # Criterio retry_job: subs PASS conservados en reset completo de fase.
    assert by_id["writer"]["subs"]["done"] == 3
    for pid in ("planner", "research", "outline"):
        assert by_id[pid]["status"] == "PASS", pid


def test_reset_from_phase_writer_en_included_in_bilingual_job():
    job = _make_job(with_writer_en=True)
    autopilot.reset_from_phase(job, "writer", chapter_number=None)
    assert _phases_by_id(job)["writer_en"]["status"] == "PENDING"


def test_reset_from_phase_rejects_running_job():
    job = _make_job(status="RUNNING")
    with pytest.raises(ValueError, match="ya activo"):
        autopilot.reset_from_phase(job, "image_gen")


def test_reset_from_phase_invalid_chapter_number():
    job = _make_job()
    with pytest.raises(ValueError, match="no existe"):
        autopilot.reset_from_phase(job, "image_gen", chapter_number=9)


def test_reset_from_phase_alias_book_planner():
    job = _make_job()
    out = autopilot.reset_from_phase(job, "book_planner", chapter_number=None)
    # Alias normalizado: la cascada de planner toca TODO el pipeline.
    for p in out["phases"]:
        assert p["status"] == "PENDING", p["id"]
    assert out["status"] == "PENDING"


def test_reset_from_phase_docx_no_cascade():
    job = _make_job()
    out = autopilot.reset_from_phase(job, "docx", chapter_number=None)
    by_id = _phases_by_id(out)
    assert by_id["docx"]["status"] == "PENDING"
    # Intactas, incluida quality_gate que conservaba su FAIL previo
    # (el reset de docx NO debe sanear ni alterar fases anteriores).
    assert by_id["quality_gate"]["status"] == "FAIL"
    for pid in ("planner", "research", "writer", "image_gen"):
        assert by_id[pid]["status"] == "PASS", pid


def test_reset_from_phase_tolerates_missing_image_plan():
    """Ratio=0: image_plan no existe en el job -> skip silencioso."""
    job = _make_job()
    job["phases"] = [p for p in job["phases"] if p["id"] != "image_plan"]
    out = autopilot.reset_from_phase(job, "image_gen", chapter_number=None)
    by_id = _phases_by_id(out)
    assert by_id["image_gen"]["status"] == "PENDING"
    assert by_id["quality_gate"]["status"] == "PENDING"


def test_reset_from_phase_unknown_phase():
    job = _make_job()
    with pytest.raises(ValueError, match="desconocida"):
        autopilot.reset_from_phase(job, "no_existe")


def test_reset_from_phase_image_gen_full_book_resets_pass_subs_with_deficit():
    """§17 #30 / Fix B: image_gen puede estar PASS con déficit tolerado
    (capítulo PASS con menos imágenes de las solicitadas). Al resetear desde
    image_gen (chapter_number=None), TODOS los subs PASS deben volver a
    PENDING para forzar la regeneración — NO conservarlos como en writer.
    """
    job = _make_job()
    # image_gen está PASS con subs PASS (simulan déficit tolerado §17 #30).
    ig = _phases_by_id(job)["image_gen"]
    assert ig["status"] == "PASS"
    assert all(
        s["status"] == "PASS" for s in ig["subs"]["chapters"].values()
    )

    out = autopilot.reset_from_phase(job, "image_gen", chapter_number=None)
    by_id = _phases_by_id(out)
    ig = by_id["image_gen"]
    assert ig["status"] == "PENDING"
    assert ig["attempts"] == 0
    # Todos los subs vuelven a PENDING (forzar regeneración de imágenes).
    assert all(
        s["status"] == "PENDING" for s in ig["subs"]["chapters"].values()
    )
    assert ig["subs"]["done"] == 0
    # Cascada reseteada.
    assert by_id["quality_gate"]["status"] == "PENDING"
    assert by_id["docx"]["status"] == "PENDING"
    # Fases ANTERIORES a image_gen intactas.
    for pid in ("planner", "research", "outline", "writer", "fact_check",
                "editor", "image_plan"):
        assert by_id[pid]["status"] == "PASS", pid


def test_reset_from_phase_writer_full_book_keeps_pass_subs():
    """Fix B NO debe afectar a writer: PASS = completo y correcto, los subs
    PASS se conservan (comportamiento retry_job intacto para texto)."""
    job = _make_job()
    out = autopilot.reset_from_phase(job, "writer", chapter_number=None)
    by_id = _phases_by_id(out)
    assert by_id["writer"]["status"] == "PENDING"
    # Subs PASS conservados (no regenerar texto válido ya escrito).
    assert by_id["writer"]["subs"]["done"] == 3
    assert all(
        s["status"] == "PASS"
        for s in by_id["writer"]["subs"]["chapters"].values()
    )