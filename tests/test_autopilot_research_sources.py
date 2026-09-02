"""Tests 8E.3 — asociación real Research -> capítulos vía core.autopilot.run_job.

Verifican que las fuentes globales devueltas por Research terminan asociadas a los
capítulos reales del libro en SourceManager, y que esa asociación llega a
`_build_book_dict` -> `Chapter.sources` -> Quality Gate.

Atraviesan el código REAL de core.autopilot.run_job (el punto que almacena y ahora
asocia las fuentes), con un executor controlado SOLO para orquestar las fases sin
red/LLM. No insertan chapter_ids a mano (a diferencia de test_editorial_sources.py).
"""
from __future__ import annotations

import os

import pytest

from core import autopilot
from core.autopilot import BookJobStore, create_job, run_job
from core.book.source_manager import SourceManager
from core.database import init_db
from frontend.editorial import (
    create_book,
    persist_chapter_result,
    _get_book,
    _get_chapters,
    _build_book_dict,
)
from modules.quality_control.main import final_quality_control


SOURCES_GLOBAL = [
    {"url": "https://real.example/a", "title": "Fuente A", "source_type": "web_wikipedia"},
    {"url": "https://real.example/b", "title": "Fuente B", "source_type": "web_wikipedia"},
]


@pytest.fixture
def store(tmp_path):
    jobs_dir = os.path.join(str(tmp_path), "jobs")
    os.makedirs(jobs_dir, exist_ok=True)
    return BookJobStore(jobs_dir)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", os.path.join(str(tmp_path), "t8e3.db"))
    init_db()


def _make_executor(sources):
    """Executor controlado: planner/research/outline pasan; research aporta fuentes.
    El resto (writer, etc.) detiene el job; la asociación ya quedó persistida en research."""
    def _exec(phase, job):
        if phase["id"] == "research":
            return autopilot.PhaseResult(
                ok=True,
                metrics={"sources": sources, "source_count": len(sources)},
                module="research",
            )
        if phase["id"] in ("planner", "outline"):
            return autopilot.PhaseResult(ok=True, metrics={}, module="test")
        return autopilot.PhaseResult(ok=False, error="stop-for-test")
    return _exec


def _run_research(store, book_id, sources):
    """Ejecuta run_job hasta completar la fase research (asociación incluida)."""
    job = create_job(store, book_id, {"idea": "test", "target_chapters": 1})
    run_job(job, store, _make_executor(sources), max_attempts=1, sleep_fn=lambda s: None)
    return job


def _chapter_id(book_id: int) -> int:
    return _get_chapters(book_id)[0]["id"]


# A) una fuente de research termina asociada al capítulo real
def test_a_research_source_associated_to_real_chapter(store):
    b = create_book({"title": "Libro", "target_chapters": 1})
    _run_research(store, b["book_id"], SOURCES_GLOBAL)
    urls = [s.get("url") for s in SourceManager.get_chapter_sources(_chapter_id(b["book_id"]))]
    assert "https://real.example/a" in urls


# B) _build_book_dict devuelve esa URL dentro de chapters[0]["sources"]
def test_b_build_book_dict_includes_associated_url(store):
    b = create_book({"title": "Libro", "target_chapters": 1})
    cid = _chapter_id(b["book_id"])
    persist_chapter_result(b["book_id"], cid, "draft_es", "Capítulo con texto suficiente para materializarlo.")
    _run_research(store, b["book_id"], SOURCES_GLOBAL)
    d = _build_book_dict(_get_book(b["book_id"]), _get_chapters(b["book_id"]))
    assert "https://real.example/a" in d["chapters"][0]["sources"]


# C) multi-capítulo: la fuente global se asocia a TODOS los capítulos reales
def test_c_multichapter_research_source_associated_to_all_chapters(store):
    b = create_book({"title": "Libro", "target_chapters": 3})
    _run_research(store, b["book_id"], SOURCES_GLOBAL)
    for c in _get_chapters(b["book_id"]):
        urls = [s.get("url") for s in SourceManager.get_chapter_sources(c["id"])]
        assert "https://real.example/a" in urls
        assert "https://real.example/b" in urls


# D) Quality Gate recibe las fuentes vía Chapter.sources (no job.data.sources)
def test_d_quality_gate_receives_chapter_sources(store):
    b = create_book({"title": "Libro", "target_chapters": 1})
    cid = _chapter_id(b["book_id"])
    persist_chapter_result(b["book_id"], cid, "draft_es", "Capítulo con texto suficiente.")
    _run_research(store, b["book_id"], SOURCES_GLOBAL)
    d = _build_book_dict(_get_book(b["book_id"]), _get_chapters(b["book_id"]))
    assert d["chapters"][0]["sources"]  # llega via Chapter.sources en el book_dict
    out = final_quality_control({"book": d, "language": "es"})
    sc = out["source_checks"]
    assert sc, "source_checks no fue generado"
    # Demuestra que el QC recibe las fuentes vía Chapter.sources: el check de
    # fuentes presentes debe ser PASS. (El check "Investigación faltante" es un
    # WARNING no relacionado con fuentes y no debe convertirse en FAIL.)
    assert any(
        x["status"] == "PASS" and "Fuentes presentes" in x["message"] for x in sc
    ), sc


# E) deduplicación: la misma fuente no se duplica aunque research repita entradas
def test_e_source_not_duplicated_when_repeated(store):
    b = create_book({"title": "Libro", "target_chapters": 1})
    dup = {"url": "https://real.example/dup", "title": "Dup", "source_type": "web_wikipedia"}
    _run_research(store, b["book_id"], [dup, dict(dup)])  # entrada repetida
    urls = [s.get("url") for s in SourceManager.get_chapter_sources(_chapter_id(b["book_id"]))]
    assert urls.count("https://real.example/dup") == 1


# F) job.data.sources conserva las fuentes originales
def test_f_job_data_sources_preserved(store):
    b = create_book({"title": "Libro", "target_chapters": 1})
    _run_research(store, b["book_id"], SOURCES_GLOBAL)
    stored = store.load_by_book(b["book_id"])
    assert [s["url"] for s in stored["data"]["sources"]] == [
        "https://real.example/a", "https://real.example/b",
    ]


# ---------------------------------------------------------------------------
# G/H — Research parametrizado por idioma (fix book_56 / §19 P3).
# Reutiliza el patrón de test_autopilot_document_output.py: executor de
# producción REAL (default_executor_factory) con un módulo research FALSO que
# registra las llamadas y devuelve fuentes etiquetadas por idioma.
# ---------------------------------------------------------------------------
def _fake_research_module(calls: list):
    """Módulo research controlado: registra el idioma de cada llamada y devuelve
    3 fuentes de la Wikipedia del idioma pedido (pasa el gate de min_sources=3)."""

    def research_execute(payload, capability="research_web"):
        lang = str(payload.get("language") or "es")
        calls.append(lang)
        srcs = [
            {
                "url": f"https://{lang}.wikipedia.org/wiki/Art{i}",
                "title": f"Art{i} ({lang})",
                "source_type": "web_wikipedia",
            }
            for i in range(1, 4)
        ]
        return {
            "status": "PASS",
            "execution_mode": "real",
            "query": payload.get("query") or "",
            "language": lang,
            "sources": srcs,
            "stored_sources": [],
            "source_count": len(srcs),
            "error": None,
            "quality_gate": "PASS",
        }

    return {
        "research": {
            "manifest": {"id": "research", "config": {"timeout_seconds": 30}},
            "execute": research_execute,
        }
    }


def _job_ready_at_research(store, book_id, data=None):
    job = autopilot.create_job(store, book_id, data or {"idea": "x", "target_chapters": 1})
    for ph in job["phases"]:
        ph["status"] = (
            autopilot.PHASE_PENDING if ph["id"] == "research" else autopilot.PHASE_PASS
        )
        if ph["id"] == "research":
            ph["attempts"] = 0
    store.save(job)
    return store.load(job["job_id"])


def test_g_bilingual_book_runs_research_once_per_language(store):
    """Libro bilingüe (es,en): research se ejecuta EXACTAMENTE 1 vez por idioma
    (2 llamadas totales), cada pasada consulta su Wikipedia, y las fuentes quedan
    separadas por idioma en job.data.sources_by_lang sin mezcla ni duplicados."""
    b = create_book({"title": "Libro bilingüe", "target_chapters": 1, "language": "es,en"})
    calls: list = []
    modules = _fake_research_module(calls)
    executor = autopilot.default_executor_factory(modules, {"research_web": ["research"]}, store)
    job = _job_ready_at_research(store, b["book_id"])
    final = autopilot.run_job(job, store, executor, max_attempts=1, sleep_fn=lambda s: None)

    res = next(p for p in final["phases"] if p["id"] == "research")
    assert res["status"] == autopilot.PHASE_PASS
    # Exactamente UNA llamada por idioma, 2 en total.
    assert sorted(calls) == ["en", "es"]

    data = final["data"]
    # Separación por idioma, SIN mezcla.
    assert set((data.get("sources_by_lang") or {}).keys()) == {"es", "en"}
    for lang in ("es", "en"):
        srcs = data["sources_by_lang"][lang]
        assert len(srcs) == 3
        assert all(s["url"].startswith(f"https://{lang}.wikipedia.org/") for s in srcs), srcs
    # Fusión histórica (job.data.sources): unión deduplicada por URL, 6 únicas.
    merged_urls = [s["url"] for s in data["sources"]]
    assert len(merged_urls) == 6
    assert len(set(merged_urls)) == 6


def test_h_monolingual_es_book_still_single_research_call(store):
    """Regresión cero: libro monolingüe 'es' → UNA sola llamada total (sin pasada
    EN) y comportamiento histórico intacto (sin sources_by_lang)."""
    b = create_book({"title": "Libro español", "target_chapters": 1})  # languages='es'
    calls: list = []
    modules = _fake_research_module(calls)
    executor = autopilot.default_executor_factory(modules, {"research_web": ["research"]}, store)
    job = _job_ready_at_research(store, b["book_id"])
    final = autopilot.run_job(job, store, executor, max_attempts=1, sleep_fn=lambda s: None)

    res = next(p for p in final["phases"] if p["id"] == "research")
    assert res["status"] == autopilot.PHASE_PASS
    assert calls == ["es"]
    data = final["data"]
    assert [s["url"] for s in data["sources"]] == [
        f"https://es.wikipedia.org/wiki/Art{i}" for i in range(1, 4)
    ]
    assert "sources_by_lang" not in data


# ---------------------------------------------------------------------------
# §17 #20 PASO 3 — build_payload("outline") incluye fuentes resumidas
# ---------------------------------------------------------------------------

def test_outline_payload_includes_summarized_sources(store):
    """La fase outline recibe las fuentes reales de job.data (título + resumen
    300 chars), para que book_planner pueda anclar el outline (§17 #20)."""
    from frontend.editorial import build_payload

    b = create_book({"title": "Libro Outline", "target_chapters": 1})
    sources = [
        {
            "url": "https://real.example/a",
            "title": "Fuente A",
            "source_type": "web_wikipedia",
            "content": "x" * 500,
        }
    ]
    data = {"sources_by_lang": {"es": sources}, "idea": "Historia de prueba"}
    payload = build_payload(b["book_id"], "outline", data, language="es")
    assert payload["sources"] is not None
    src = payload["sources"][0]
    assert src["title"] == "Fuente A"
    assert len(src["summary"]) == 300  # resumido, no el contenido completo


def test_outline_payload_without_sources_is_none(store):
    """Sin research (lista vacía), sources va None — comportamiento previo intacto."""
    from frontend.editorial import build_payload

    b = create_book({"title": "Libro Sin Research", "target_chapters": 1})
    payload = build_payload(b["book_id"], "outline", {"idea": "Novela"}, language="es")
    assert payload["sources"] is None


# ---------------------------------------------------------------------------
# FIX book_62 — fallback research bilingüe: la pasada del idioma SECUNDARIO
# que falla SOLO por gate de source_count insuficiente no aborta el job.
# ---------------------------------------------------------------------------
def _fake_research_module_en_short(calls: list, es_sources=None):
    """Módulo research controlado (caso book_62 real): pasada 'es' PASS con 5
    fuentes; pasada 'en' FAIL por gate source_count=1 < min_sources=3 (el error
    es EXACTAMENTE el string que construye _run_single para research)."""

    def research_execute(payload, capability="research_web"):
        lang = str(payload.get("language") or "es")
        calls.append(lang)
        if lang == "es":
            srcs = es_sources or [
                {
                    "url": f"https://es.wikipedia.org/wiki/ES{i}",
                    "title": f"ES{i}",
                    "source_type": "web_wikipedia",
                }
                for i in range(1, 6)
            ]
            return {
                "status": "PASS",
                "execution_mode": "real",
                "query": payload.get("query") or "",
                "language": lang,
                "sources": srcs,
                "stored_sources": [],
                "source_count": len(srcs),
                "error": None,
                "quality_gate": "PASS",
            }
        # Pasada 'en' con solo 1 fuente (título español sin traducir → filtro
        # de anclaje descarta casi todo). El gate lo construye _run_single.
        one = [{
            "url": "https://en.wikipedia.org/wiki/Longevity",
            "title": "Longevity",
            "source_type": "web_wikipedia",
        }]
        return {
            "status": "FAIL",
            "execution_mode": "llm",
            "query": payload.get("query") or "",
            "language": lang,
            "sources": one,
            "stored_sources": [],
            "source_count": 1,
            "error": None,
            "quality_gate": "FAIL",
        }

    return {
        "research": {
            "manifest": {"id": "research", "config": {"timeout_seconds": 30}},
            "execute": research_execute,
        }
    }


def test_i_bilingual_secondary_lang_low_source_count_falls_back(store):
    """book_62: libro bilingüe donde la pasada 'en' devuelve 1 fuente (<min=3).
    La fase NO aborta el job: sources_by_lang['en'] = copia de sources_by_lang
    ['es'], warning registrado en las métricas, fase PASS. La lista global
    job.data.sources NO se duplica."""
    b = create_book({"title": "Libro bilingüe", "target_chapters": 1, "language": "es,en"})
    calls: list = []
    modules = _fake_research_module_en_short(calls)
    executor = autopilot.default_executor_factory(modules, {"research_web": ["research"]}, store)
    job = _job_ready_at_research(store, b["book_id"])
    final = autopilot.run_job(job, store, executor, max_attempts=1, sleep_fn=lambda s: None)

    # (1) el job NO aborta en research; ambas pasadas se ejecutaron.
    assert sorted(calls) == ["en", "es"]
    res = next(p for p in final["phases"] if p["id"] == "research")
    assert res["status"] == autopilot.PHASE_PASS

    data = final["data"]
    sb = data.get("sources_by_lang") or {}
    # (2) fallback: sources_by_lang["en"] == copia de sources_by_lang["es"]
    assert sb["en"] == sb["es"]
    assert len(sb["en"]) == 5
    # job.data.sources (lista global fusionada) SIN duplicados del fallback:
    # solo las fuentes reales de la pasada primaria; la única fuente "en" que
    # devolvió la pasada fallida NO entra en la fusión (no pasó el gate).
    urls = [s["url"] for s in data["sources"]]
    assert urls.count("https://en.wikipedia.org/wiki/Longevity") == 0
    assert len(urls) == 5

    # (3) warning registrado en las métricas de la fase.
    warns = res.get("metrics", {}).get("warnings") or []
    assert any("research idioma en" in w and "source_count" in w for w in warns), warns
    assert res["metrics"]["per_language_status"]["en"] == "FALLBACK"


# ---------------------------------------------------------------------------
# §17 #44 — fallback multi-query por títulos de capítulo (book_80)
# ---------------------------------------------------------------------------
def _fake_research_module_generic_short(calls: list, generic_query: str):
    """Módulo research controlado (caso book_80 real): el query genérico del
    libro devuelve 1 sola fuente (gate FAIL por source_count < min=3);
    cualquier otro query (títulos de capítulo) devuelve 3 fuentes, con una URL
    única por query para demostrar que el fallback añadió valor real."""

    def research_execute(payload, capability="research_web"):
        query = str(payload.get("query") or "")
        lang = str(payload.get("language") or "es")
        calls.append(query)
        if query == generic_query:
            srcs = [
                {
                    "url": "https://es.wikipedia.org/wiki/Generica",
                    "title": "Genérica",
                    "source_type": "web_wikipedia",
                }
            ]
        else:
            slug = query.replace(" ", "_")
            srcs = [
                {
                    "url": f"https://es.wikipedia.org/wiki/{slug}",
                    "title": query,
                    "source_type": "web_wikipedia",
                },
                {
                    "url": f"https://es.wikipedia.org/wiki/{slug}_extra1",
                    "title": f"{query} — extra 1",
                    "source_type": "web_wikipedia",
                },
                {
                    "url": f"https://es.wikipedia.org/wiki/{slug}_extra2",
                    "title": f"{query} — extra 2",
                    "source_type": "web_wikipedia",
                },
            ]
        return {
            "status": "PASS",
            "execution_mode": "real",
            "query": query,
            "language": lang,
            "sources": srcs,
            "stored_sources": [],
            "source_count": len(srcs),
            "error": None,
            "quality_gate": "PASS",
        }

    return {
        "research": {
            "manifest": {"id": "research", "config": {"timeout_seconds": 30}},
            "execute": research_execute,
        }
    }


def test_j_research_source_shortage_recovered_via_chapter_titles(store):
    """§17 #44 (book_80): libro monolingüe 'es' cuyo query genérico (título del
    libro) devuelve 1 fuente (< min=3) → la pasada normal falla el gate, pero
    el fallback multi-query reintenta con los títulos de capítulo (que SÍ
    devuelven fuentes), fusiona sin duplicados y la fase termina PASS con
    execution_mode='multi_query_fallback'. Antes del fix la fase habría
    abortado el job con gate_fail."""
    generic = "Musica Española de los años 70 - 90"
    b = create_book({"title": generic, "target_chapters": 3})  # languages='es'
    calls: list = []
    modules = _fake_research_module_generic_short(calls, generic)
    executor = autopilot.default_executor_factory(modules, {"research_web": ["research"]}, store)
    job = _job_ready_at_research(store, b["book_id"], {"idea": generic, "target_chapters": 3})
    final = autopilot.run_job(job, store, executor, max_attempts=1, sleep_fn=lambda s: None)

    # (1) La fase research termina PASS (antes del fix: FAIL por gate).
    res = next(p for p in final["phases"] if p["id"] == "research")
    assert res["status"] == autopilot.PHASE_PASS

    # (2) Llamadas: 1 con el query genérico + 1 por título de capítulo (3 capítulos).
    assert calls[0] == generic
    assert len(calls) == 1 + 3, calls
    assert all(q != generic for q in calls[1:]), calls

    # (3) Métricas del fallback.
    metrics = res["metrics"]
    assert metrics["execution_mode"] == "multi_query_fallback"
    assert metrics["source_count"] >= 3
    assert metrics["chapter_queries_used"] == calls[1:]

    # (4) Las fuentes fusionadas incluyen al menos una que SOLO aparece en la
    # búsqueda por capítulo (las "_extra*" solo las devuelven queries de
    # capítulo) y no hay duplicados.
    data = final["data"]
    urls = [s["url"] for s in data["sources"]]
    assert any(u.endswith("_extra1") for u in urls), urls
    assert len(urls) == len(set(urls)), urls
    data = final["data"]
    urls = [s["url"] for s in data["sources"]]
    assert any(u.endswith("_extra1") for u in urls), urls
    assert len(urls) == len(set(urls)), urls


# ---------------------------------------------------------------------------
# §17 #49 — entidades de books.description cuando los títulos de capítulo son
# todos idénticos (book_85: planner en fallback)
# ---------------------------------------------------------------------------
def _fake_research_module_identical_titles(calls: list, failing_queries: set):
    """Caso book_85 real: el query genérico (título del libro) y el título de
    capítulo compartido devuelven 1 sola fuente (sin cobertura de los artistas);
    los nombres propios extraídos de description SÍ devuelven 3 fuentes cada
    uno, con URL única por query."""

    def research_execute(payload, capability="research_web"):
        query = str(payload.get("query") or "")
        lang = str(payload.get("language") or "es")
        calls.append(query)
        if query in failing_queries:
            srcs = [
                {
                    "url": f"https://es.wikipedia.org/wiki/{query.replace(' ', '_')}",
                    "title": query,
                    "source_type": "web_wikipedia",
                }
            ]
        else:
            slug = query.replace(" ", "_")
            srcs = [
                {
                    "url": f"https://es.wikipedia.org/wiki/{slug}",
                    "title": query,
                    "source_type": "web_wikipedia",
                },
                {
                    "url": f"https://es.wikipedia.org/wiki/{slug}_extra1",
                    "title": f"{query} — extra 1",
                    "source_type": "web_wikipedia",
                },
                {
                    "url": f"https://es.wikipedia.org/wiki/{slug}_extra2",
                    "title": f"{query} — extra 2",
                    "source_type": "web_wikipedia",
                },
            ]
        return {
            "status": "PASS",
            "execution_mode": "real",
            "query": query,
            "language": lang,
            "sources": srcs,
            "stored_sources": [],
            "source_count": len(srcs),
            "error": None,
            "quality_gate": "PASS",
        }

    return {
        "research": {
            "manifest": {"id": "research", "config": {"timeout_seconds": 30}},
            "execute": research_execute,
        }
    }


def test_k_identical_chapter_titles_recovered_via_description_entities(store):
    """§17 #49 (book_85): los 3 capítulos comparten el MISMO título (heredado
    de la description por el planner en fallback) y ese título también falla
    (1 fuente < min=3). El fallback multi-query no puede aportar queries
    distintivas con los títulos → extrae nombres propios de books.description
    ("La Pantoja, Chiquetete, Rocío Jurado...") y los usa como queries
    adicionales, respetando el tope combinado. Métricas enriquecidas con
    description_entities_used; execution_mode sigue siendo
    'multi_query_fallback'."""
    generic = "Musica Española de los años 1970 - 1980 y 1990"
    shared_title = f"{generic} - Parte 1"
    b = create_book(
        {
            "title": generic,
            "description": "La Pantoja, Chiquetete, Rocío Jurado y Manuel Alejandro",
            "target_chapters": 3,
        }
    )
    # book_85 real: los 3 capítulos tienen el MISMO título.
    from core.database import get_db

    conn = get_db()
    try:
        conn.execute(
            "UPDATE chapters SET title = ? WHERE book_id = ?",
            (shared_title, b["book_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    calls: list = []
    modules = _fake_research_module_identical_titles(
        calls, failing_queries={generic, shared_title}
    )
    executor = autopilot.default_executor_factory(modules, {"research_web": ["research"]}, store)
    job = _job_ready_at_research(store, b["book_id"], {"idea": generic, "target_chapters": 3})
    final = autopilot.run_job(job, store, executor, max_attempts=1, sleep_fn=lambda s: None)

    # (1) La fase research termina PASS.
    res = next(p for p in final["phases"] if p["id"] == "research")
    assert res["status"] == autopilot.PHASE_PASS

    # (2) Llamadas: genérico + título compartido (1 por dedup) + entidades de
    # description. Los títulos NO se repiten 3 veces (dedup), y las entidades
    # no incluyen el determinante ("Pantoja", no "La Pantoja").
    assert calls[0] == generic, calls
    assert calls[1] == shared_title, calls
    entity_calls = calls[2:]
    assert len(entity_calls) == len(set(entity_calls)), calls
    assert "Rocío Jurado" in entity_calls, calls
    assert "Pantoja" in entity_calls and "La Pantoja" not in entity_calls, calls
    # Tope combinado: 1 título + máx 4 entidades.
    assert len(entity_calls) <= 4, calls

    # (3) Métricas: modo sin cambios + campo nuevo.
    metrics = res["metrics"]
    assert metrics["execution_mode"] == "multi_query_fallback"
    assert metrics["chapter_queries_used"] == [shared_title]
    assert "Rocío Jurado" in (metrics.get("description_entities_used") or [])

    # (4) Las fuentes fusionadas incluyen las del nombre que sí funcionó
    # (URLs únicas del stub de entidades) y no hay duplicados.
    urls = [s["url"] for s in final["data"]["sources"]]
    assert "https://es.wikipedia.org/wiki/Rocío_Jurado" in urls, urls
    assert len(urls) == len(set(urls)), urls


def test_l_identical_titles_videogames_entities_book91_style(store):
    """§17 #49 (book_91): caso real de videojuegos — los 24 capítulos comparten
    el título del planner en fallback ('Historia de los videojuegos, desde el
    pong hasta... - Parte N') y la pasada normal falla por source_count. Las
    entidades de description (consolas/juegos Title-Case: Nintendo, Sega
    Genesis, Atari, Xbox, Minecraft) enriquecen las queries por capítulo:
    dejan de ser idénticas y description_entities_used > 0."""
    generic = "Historia de los videojuegos, desde el pong hasta..."
    shared_title = f"{generic} - Parte 1"
    b = create_book(
        {
            "title": generic,
            "description": (
                "La historia de Nintendo, Sega Genesis y Atari, con Xbox y "
                "Minecraft como fenómenos modernos."
            ),
            "target_chapters": 3,
        }
    )
    from core.database import get_db

    conn = get_db()
    try:
        conn.execute(
            "UPDATE chapters SET title = ? WHERE book_id = ?",
            (shared_title, b["book_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    calls: list = []
    modules = _fake_research_module_identical_titles(
        calls, failing_queries={generic, shared_title}
    )
    executor = autopilot.default_executor_factory(modules, {"research_web": ["research"]}, store)
    job = _job_ready_at_research(store, b["book_id"], {"idea": generic, "target_chapters": 3})
    final = autopilot.run_job(job, store, executor, max_attempts=1, sleep_fn=lambda s: None)

    # (1) research PASS vía multi_query_fallback (antes del fix: FAIL del job).
    res = next(p for p in final["phases"] if p["id"] == "research")
    assert res["status"] == autopilot.PHASE_PASS

    # (2) Queries por capítulo YA NO son idénticas: genérico + título compartido
    # (dedup) + entidades de description (videojuegos/consolas).
    assert calls[0] == generic, calls
    assert calls[1] == shared_title, calls
    entity_calls = calls[2:]
    assert entity_calls, calls
    assert len(set(entity_calls)) == len(entity_calls), calls

    # (3) Métricas: description_entities_used > 0 y modo sin cambios.
    metrics = res["metrics"]
    assert metrics["execution_mode"] == "multi_query_fallback"
    used = metrics.get("description_entities_used") or []
    assert len(used) > 0, calls
    # Entidades reconocibles del dominio videojuegos (regex Title-Case sin
    # acrónimos puros: "Nintendo", "Sega Genesis", "Atari", "Xbox", "Minecraft").
    for name in used:
        assert name in (
            "Nintendo",
            "Sega Genesis",
            "Atari",
            "Xbox",
            "Minecraft",
        ), used

    # (4) Fuentes fusionadas sin duplicados, con las de las entidades.
    urls = [s["url"] for s in final["data"]["sources"]]
    assert "https://es.wikipedia.org/wiki/Nintendo" in urls, urls
    assert len(urls) == len(set(urls)), urls