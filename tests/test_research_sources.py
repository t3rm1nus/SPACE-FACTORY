"""Fix 8 source_count (Tarea 1) — el mínimo de fuentes sigue garantizándose.

Confirma que el refactor multi-fuente de research NO rompió el fix del autopilot:
en ``core/autopilot.py`` la fase ``research`` traduce su gate real a ok=True/False
basándose en ``source_count`` vs ``min_sources`` del payload. Se ejercita la
orquestación REAL:

    run_job -> default_executor_factory -> _run_single -> scheduler._process_task
        -> módulo 'research' (real, con backends mockeados) -> PhaseResult -> gate

Casos:
- source_count >= min_sources  -> research PASS, sin gate_fail, job COMPLETED.
- source_count <  min_sources  -> research FAIL con error de min_sources, job FAILED.
"""
from __future__ import annotations

import os

import pytest

import modules.research.main as research
from core import autopilot
from core.database import init_db
from frontend.editorial import create_book

_NOSLEEP = lambda _s: None  # noqa: E731 (evita esperas reales en tests)

_CANDIDATES = [
    {"url": "https://es.wikipedia.org/wiki/Gato", "title": "Gato", "source_type": "web_wikipedia",
     "content": "El gato doméstico es un mamífero carnívoro de la familia Felidae.", "snippet": "felino"},
    {"url": "https://es.wikipedia.org/wiki/Gato_domestic", "title": "Gato doméstico", "source_type": "web_wikipedia",
     "content": "El gato doméstico, también conocido como gato casero, es un mamífero de la familia Felidae.", "snippet": "gato"},
    # Candidato irrelevante: overlap=0 con "gato" → filtrado por RELEVANCE_MIN_OVERLAP
    {"url": "https://es.wikipedia.org/wiki/Crozet", "title": "Crozet (Virginia)", "source_type": "web_wikipedia",
     "content": "Crozet es un lugar designado por el censo en el condado de Albemarre, Virginia.", "snippet": "censo"},
]


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", os.path.join(str(tmp_path), "t_sources.db"))
    init_db()


@pytest.fixture(autouse=True)
def _stub_searxng(monkeypatch):
    """SearXNG (FASE 8M.2) stubbeado a [] aquí: estos tests son aislados de red real
    (orquestación autopilot + filtro de relevancia). El backend contra el contenedor
    local se prueba en tests/test_research_searxng.py."""
    monkeypatch.setattr(research, "_search_searxng", lambda q, n, timeout: [])


@pytest.fixture
def store(tmp_path):
    return autopilot.BookJobStore(os.path.join(str(tmp_path), "jobs"))


def _mock_research_backends(monkeypatch) -> None:
    """Aísla el módulo research para que produzca 2 fuentes sin red ni LLM."""
    monkeypatch.setattr(research, "RESEARCH_USE_LLM", "0")  # ranking determinista
    monkeypatch.setattr(research, "_backend_wikipedia", lambda q, n, timeout: list(_CANDIDATES))
    monkeypatch.setattr(research, "_backend_wikidata", lambda q, n, timeout: [])


def _research_execute(payload):
    return research.execute(payload)


def _executor():
    modules = {
        "research": {
            "manifest": {"id": "research", "config": {"timeout_seconds": 60}},
            "execute": _research_execute,
        }
    }
    cap_map = {"research_web": ["research"]}
    return autopilot.default_executor_factory(modules, cap_map)


def _job_ready(store, book_id, min_sources: int):
    job = autopilot.create_job(store, book_id, {"idea": "gato", "target_chapters": 1})
    job["data"]["max_sources"] = 5
    job["data"]["min_sources"] = min_sources
    for ph in job["phases"]:
        if ph["id"] == "research":
            ph["status"] = autopilot.PHASE_PENDING
            ph["attempts"] = 0
        else:
            ph["status"] = autopilot.PHASE_PASS
            ph["attempts"] = 1
    store.save(job)
    return store.load(job["job_id"])


def _run(store, book_id, min_sources):
    executor = _executor()
    job = _job_ready(store, book_id, min_sources)
    collected: list = []
    emit = lambda ev, d: collected.append((ev, d))  # noqa: E731
    final = autopilot.run_job(job, store, executor, emit=emit, max_attempts=2, sleep_fn=_NOSLEEP)
    return final, collected


def test_source_count_ge_min_keeps_gate_fail_none(monkeypatch, store):
    """source_count >= min_sources => research PASS, sin gate_fail, job COMPLETED."""
    _mock_research_backends(monkeypatch)
    book_id = create_book({"title": "Gato", "target_chapters": 1})["book_id"]
    final, collected = _run(store, book_id, min_sources=2)

    research_phase = next(p for p in final["phases"] if p["id"] == "research")
    assert research_phase["status"] == autopilot.PHASE_PASS
    assert research_phase["error"] is None
    metrics = research_phase.get("metrics") or {}
    assert int(metrics.get("source_count") or 0) >= 2
    assert metrics.get("status") == "PASS"
    assert metrics.get("quality_gate") == "PASS"
    assert final["status"] == autopilot.JOB_COMPLETED
    assert "job_completed" in [ev for ev, _ in collected]


def test_source_count_lt_min_triggers_gate_fail(monkeypatch, store):
    """Si research no llega al mínimo, el gate del autopilot lo traduce a FAIL."""
    _mock_research_backends(monkeypatch)
    book_id = create_book({"title": "Libro", "target_chapters": 1})["book_id"]
    final, _ = _run(store, book_id, min_sources=5)  # 5 > las 2 fuentes reales

    research_phase = next(p for p in final["phases"] if p["id"] == "research")
    assert research_phase["status"] != autopilot.PHASE_PASS
    assert "source_count" in (research_phase.get("error") or "")
    assert "min=" in (research_phase.get("error") or "")
    assert final["status"] != autopilot.JOB_COMPLETED


# ---- Tests del filtro de relevancia (Fase 2: gate de relevancia en research) ----


def _keyword_overlap_buggy(query: str, cand: dict) -> float:
    """Implementación previa al fix: substring sin stopwords (reproduce el bug libro #18)."""
    import re

    keywords = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) >= 2]
    if not keywords:
        return 0.0
    haystack = (
        str(cand.get("title") or "").lower()
        + " "
        + str(cand.get("snippet") or "").lower()
        + " "
        + str(cand.get("content") or "").lower()
    )
    hits = sum(1 for w in keywords if w in haystack)
    return hits / len(keywords)


_LOS_DOOMS_QUERY = "Los Dooms: El Último"
_LOS_DOOMS_IRRELEVANT = [
    {"url": "https://es.wikipedia.org/wiki/Crozet", "title": "Crozet (Virginia)",
     "source_type": "web_wikipedia", "content": "Crozet es un lugar designado por el censo.",
     "snippet": "censo"},
    {"url": "https://es.wikipedia.org/wiki/Crimora", "title": "Crimora (Virginia)",
     "source_type": "web_wikipedia", "content": "Crimora es un lugar designado por el censo.",
     "snippet": "censo"},
    {"url": "https://es.wikipedia.org/wiki/Sam_Porter_Bridges", "title": "Sam Porter Bridges",
     "source_type": "web_wikipedia",
     "content": "Sam Porter Bridges es el protagonista ficticio de Death Stranding.",
     "snippet": "personaje"},
    {"url": "https://es.wikipedia.org/wiki/Agente_espumante", "title": "Agente espumante",
     "source_type": "web_wikipedia", "content": "Agente espumante es un término químico.",
     "snippet": "espumante"},
]

_IRRELEVANT_CANDIDATES = [
    {"url": "https://es.wikipedia.org/wiki/Crozet", "title": "Crozet (Virginia)",
     "source_type": "web_wikipedia", "content": "Crozet es un lugar designado por el censo.",
     "snippet": "censo"},
    {"url": "https://es.wikipedia.org/wiki/Spartan", "title": "Spartan (personaje)",
     "source_type": "web_wikipedia", "content": "Spartan es un personaje de ficción en otro universo.",
     "snippet": "personaje"},
]
def test_keyword_overlap_calcula_fraccion_correctamente():
    """_keyword_overlap: fracción de keywords (>=2 chars) presentes en la fuente."""
    from modules.research.main import _keyword_overlap

    # Una keyword coincide de 1 → 1.0
    assert _keyword_overlap("gato", _CANDIDATES[0]) == 1.0
    # Dos keywords, ninguna coincide → 0.0
    assert _keyword_overlap("gato perro", _IRRELEVANT_CANDIDATES[0]) == 0.0
    # Dos keywords, una coincide → 0.5
    cand = {"title": "Gato", "snippet": "", "content": ""}
    assert _keyword_overlap("gato perro", cand) == 0.5
    # Keywords de 1 carácter se ignoran
    assert _keyword_overlap("a", _CANDIDATES[0]) == 0.0


def test_relevance_filter_descarta_fuentes_irrelevantes(monkeypatch):
    """Fuentes con overlap < RELEVANCE_MIN_OVERLAP se descartan del
    conteo source_count: no se persisten ni cuentan para PASS/FAIL."""
    monkeypatch.setattr(research, "RESEARCH_USE_LLM", "0")
    monkeypatch.setattr(research, "_backend_wikipedia",
                        lambda q, n, timeout: list(_CANDIDATES))
    monkeypatch.setattr(research, "_backend_wikidata", lambda q, n, timeout: [])

    out = research.research_web("gato", max_sources=5, timeout=20)
    # _CANDIDATES tiene 2 relevantes (Gato, Gato doméstico) + 1 irrelevante (Crozet)
    # El irrelevante debe ser filtrado → source_count = 2
    assert out["source_count"] == 2, f"Esperado 2 (relevantes), obtuve {out['source_count']}"
    assert out["status"] == "PASS"
    assert out["quality_gate"] == "PASS"
    urls = {s["url"] for s in out["sources"]}
    assert "https://es.wikipedia.org/wiki/Crozet" not in urls


def test_relevance_filter_todas_irrelevantes_source_count_cero(monkeypatch):
    """Si TODAS las fuentes quedan filtradas por relevancia, source_count=0
    y el status es FAIL (no se inventa ni se relaja el umbral)."""
    monkeypatch.setattr(research, "RESEARCH_USE_LLM", "0")
    monkeypatch.setattr(research, "_backend_wikipedia",
                        lambda q, n, timeout: list(_IRRELEVANT_CANDIDATES))
    monkeypatch.setattr(research, "_backend_wikidata", lambda q, n, timeout: [])

    out = research.research_web("gato", max_sources=5, timeout=20)
    assert out["source_count"] == 0
    assert out["stored_sources"] == []
    assert out["status"] == "FAIL"
    assert out["quality_gate"] == "FAIL"
    assert out["error"] is not None


def test_relevance_filter_execute_all_irrelevant_fails(monkeypatch):
    """End-to-end: execute() con todas fuentes irrelevantes → FAIL, no PASS forzado."""
    monkeypatch.setattr(research, "RESEARCH_USE_LLM", "0")
    monkeypatch.setattr(research, "_backend_wikipedia",
                        lambda q, n, timeout: list(_IRRELEVANT_CANDIDATES))
    monkeypatch.setattr(research, "_backend_wikidata", lambda q, n, timeout: [])

    out = research.execute({"query": "gato", "max_sources": 5, "min_sources": 1,
                            "research_required": True})
    assert out["status"] == "FAIL"
    assert out["quality_gate"] == "FAIL"
    assert out["source_count"] == 0


def test_real_query_los_dooms_stopwords_filtro(monkeypatch):
    """Query real libro #18: stopwords + tokenización evitan falsos positivos.

    Keywords sin filtrar: ['los', 'dooms', 'el', 'último'].
    Con la implementación buggy, Crozet/Crimora/Sam Porter alcanzan overlap=0.250
    porque 'el' coincide como substring en el contenido ('... por el censo.').
    Tras el fix, keywords efectivas = ['dooms', 'último'] y overlap=0.0 → descartadas.
    """
    from modules.research.main import RELEVANCE_MIN_OVERLAP, _STOPWORDS_ES, _keyword_overlap

    assert "el" in _STOPWORDS_ES
    assert "los" in _STOPWORDS_ES

    before_scores: list[float] = []
    after_scores: list[float] = []
    for cand in _LOS_DOOMS_IRRELEVANT[:3]:
        before = _keyword_overlap_buggy(_LOS_DOOMS_QUERY, cand)
        after = _keyword_overlap(_LOS_DOOMS_QUERY, cand)
        before_scores.append(before)
        after_scores.append(after)
        print(
            f"  {cand['title']}: ANTES={before:.3f} DESPUÉS={after:.3f} "
            f"(umbral={RELEVANCE_MIN_OVERLAP})"
        )
        assert before >= RELEVANCE_MIN_OVERLAP, (
            f"Sanity: {cand['title']} debía PASAR con implementación buggy "
            f"(overlap={before:.3f})"
        )
        assert after < RELEVANCE_MIN_OVERLAP, (
            f"{cand['title']} debe quedar filtrada tras el fix (overlap={after:.3f})"
        )

    # Agente espumante: irrelevante en ambos casos, pero debe quedar filtrado
    agente = _LOS_DOOMS_IRRELEVANT[3]
    assert _keyword_overlap(_LOS_DOOMS_QUERY, agente) < RELEVANCE_MIN_OVERLAP

    # Integración: research_web descarta las 4 fuentes irrelevantes
    monkeypatch.setattr(research, "RESEARCH_USE_LLM", "0")
    monkeypatch.setattr(
        research, "_backend_wikipedia",
        lambda q, n, timeout: list(_LOS_DOOMS_IRRELEVANT),
    )
    monkeypatch.setattr(research, "_backend_wikidata", lambda q, n, timeout: [])

    out = research.research_web(_LOS_DOOMS_QUERY, max_sources=5, timeout=20)
    assert out["source_count"] == 0
    assert out["status"] == "FAIL"
    assert out["quality_gate"] == "FAIL"
# ---------------------------------------------------------------------------
# Fase 8I.1 — Anclaje del filtro de relevancia al TEMA del libro (topic)
# ---------------------------------------------------------------------------
# El filtro de relevancia (PASO 4) pasa de depender solo del overlap de keywords
# de la QUERY a usar un criterio compuesto: overlap de query >= umbral Y anclaje
# al topic (_has_anchor_keyword). Estos tests verifican que:
#   a) sin topic, el comportamiento NO cambia (regression guard);
#   b) con topic, una fuente con buen overlap de query pero sin ninguna keyword
#      del tema queda descartada (ancla real).


def test_anchor_topic_none_preserva_comportamiento_previo():
    """(a) Regression guard: topic None/vacío => _has_anchor_keyword devuelve
    True (no bloquea) y el filtro compuesto se comporta igual que antes.
    También topic con solo stopwords (sin keywords útiles) no añade restricción.
    """
    from modules.research.main import _has_anchor_keyword

    cand = _LOS_DOOMS_IRRELEVANT[0]
    assert _has_anchor_keyword(None, cand) is True
    assert _has_anchor_keyword("", cand) is True
    # topic con solo stopwords => sin keywords útiles => no ancla (no bloquea)
    assert _has_anchor_keyword("el los la de y con", cand) is True


def test_anchor_topic_none_integracion_mantiene_filtrado_previo(monkeypatch):
    """(a) Integration regression: research_web SIN topic conserva la detección
    de 8K.3 ('Los Dooms: El Último' vs fuentes irrelevantes => source_count=0).
    Confirma que el fix NO cambia el resultado previo cuando no hay topic."""
    from modules.research.main import RELEVANCE_MIN_OVERLAP, _keyword_overlap

    # Sanity: de forma aislada sigue detectando lo mismo que _keyword_overlap solo.
    for cand in _LOS_DOOMS_IRRELEVANT[:3]:
        assert _keyword_overlap(_LOS_DOOMS_QUERY, cand) < RELEVANCE_MIN_OVERLAP

    monkeypatch.setattr(research, "RESEARCH_USE_LLM", "0")
    monkeypatch.setattr(
        research, "_backend_wikipedia",
        lambda q, n, timeout: list(_LOS_DOOMS_IRRELEVANT),
    )
    monkeypatch.setattr(research, "_backend_wikidata", lambda q, n, timeout: [])

    out = research.research_web(_LOS_DOOMS_QUERY, max_sources=5, timeout=20)  # NO topic
    assert out["source_count"] == 0
    assert out["status"] == "FAIL"
    assert out["quality_gate"] == "FAIL"


def test_doctor_doom_marvel_no_anclaje_book37():
    """Fix book_37: topic multi-palabra "Historia del Doom" NO debe anclarse al
    artículo de Wikipedia "Doctor Doom" (supervillano Marvel), que solo comparte
    la palabra "doom" pero no "historia". Snippet real extraído de la BD
    (sources.id=624, book 37, chapter_ids=[167,168,169])."""
    from modules.research.main import _has_anchor_keyword

    doctor_doom_marvel = {
        "url": "https://es.wikipedia.org/wiki/Doctor_Doom",
        "title": "Doctor Doom",
        "source_type": "web_wikipedia",
        "snippet": "Doctor Doom o Doctor Muerte, alias del Dr. Víctor von Doom, es un supervillano",
        "content": (
            "Doctor Doom o Doctor Muerte, alias del Dr. Víctor von Doom, es un supervillano "
            "que aparece en los cómics estadounidenses publicados por Marvel Comics. Creado "
            "por Stan Lee y Jack Kirby, el personaje apareció por primera vez en The "
            "Fantastic Four #5. En sus apariciones en cómics, Doctor Doom es representado "
            "como el monarca de Latveria cuyo objetivo es traer orden a la humanidad a "
            "través de la conquista mundial. Él sirve como el archienemigo de Reed Richards "
            "y Los 4 Fantásticos, aunque también ha entrado en conflicto con los X-Men y "
            "otros superhéroes del Universo Marvel. Aunque generalmente se lo retrata como "
            "un villano, Doom también ha sido un antihéroe en ocasiones, trabajando con "
            "héroes si sus objetivos están alineados y solo si eso lo beneficia."
        ),
    }
    assert _has_anchor_keyword("Historia del Doom", doctor_doom_marvel) is False


def test_historia_del_doom_si_ancla_articulo_videojuego_1993():
    """No-regresión: topic "Historia del Doom" SÍ debe anclarse al artículo
    legítimo "Doom (videojuego de 1993)" de Wikipedia (comparte "doom" y
    "historia"/contexto del videojuego). Snippet real de la BD (sources.id=618)."""
    from modules.research.main import _has_anchor_keyword

    doom_1993 = {
        "url": "https://es.wikipedia.org/wiki/Doom_(videojuego_de_1993)",
        "title": "Doom (videojuego de 1993) - Wikipedia, la enciclopedia libre",
        "source_type": "web_wikipedia",
        "snippet": "Doom fue el tercer lanzamiento independiente importante de id Software",
        "content": (
            "Doom fue el tercer lanzamiento independiente importante de id Software, "
            "después de Commander Keen (1990-1991) y Wolfenstein 3D (1992). En mayo de "
            "1992, id comenzó a desarrollar un juego más oscuro centrado en luchar contra "
            "demonios con tecnología, utilizando un nuevo motor de juego 3D del programador "
            "principal, John Carmack. El diseñador Tom Hall inicialmente escribió una trama "
            "de ciencia ficción, pero él y la mayor parte de la historia..."
        ),
    }
    assert _has_anchor_keyword("Historia del Doom", doom_1993) is True


def test_anchor_topic_una_sola_palabra_mantiene_comportamiento():
    """No-regresión: topic de UNA sola keyword ("Doom") sigue anclando con 1 sola
    coincidencia — el fix multi-palabra no debe endurecer temas monopalabra."""
    from modules.research.main import _has_anchor_keyword

    assert _has_anchor_keyword("Doom", {"title": "Doctor Doom", "content": "supervillano de Marvel"}) is True


def test_anchor_topic_doom_descarta_edad_media_sin_mencionar_doom(monkeypatch):
    """(b) Con topic="Doom", una fuente de Edad Media histórica cuyas palabras
    de query (dark/ages) superan el overlap pero que NUNCA menciona 'doom' queda
    descartada por no anclarse al tema. Sin topic, esa misma fuente pasaba."""
    from modules.research.main import RELEVANCE_MIN_OVERLAP, _has_anchor_keyword, _keyword_overlap

    medieval_dark = {
        "url": "https://es.wikipedia.org/wiki/Dark_Ages",
        "title": "Dark Ages and early medieval England",
        "snippet": "Edad Media, reino anglosajón",
        "content": (
            "The Dark Ages describe the period in Western Europe after the fall "
            "of the western Roman Empire. La Inglaterra anglosajona se desarrolló "
            "durante la alta y la baja Edad Media."
        ),
    }
    query = "doom dark ages trucos"

    # 1) La query sí solapa con el candidato por encima del umbral (dark/ages).
    assert _keyword_overlap(query, medieval_dark) >= RELEVANCE_MIN_OVERLAP
    # 2) Pero ninguna keyword del topic 'Doom' está en el haystack => no ancla.
    assert _has_anchor_keyword("Doom", medieval_dark) is False
    # 3) Criterio compuesto: la fuente se descarta.
    keep = (
        _keyword_overlap(query, medieval_dark) >= RELEVANCE_MIN_OVERLAP
        and _has_anchor_keyword("Doom", medieval_dark)
    )
    assert keep is False

    monkeypatch.setattr(research, "RESEARCH_USE_LLM", "0")
    monkeypatch.setattr(
        research, "_backend_wikipedia",
        lambda q, n, timeout: [medieval_dark],
    )
    monkeypatch.setattr(research, "_backend_wikidata", lambda q, n, timeout: [])

    # Sin topic: behavior previo (pasa).
    out_sin = research.research_web(query, max_sources=5, timeout=20)
    assert out_sin["source_count"] == 1
    assert out_sin["status"] == "PASS"

    # Con topic="Doom": se descarta por no anclarse al tema => FAIL honesto.
    out_con = research.research_web(query, max_sources=5, timeout=20, topic="Doom")
    assert out_con["source_count"] == 0
    assert out_con["status"] == "FAIL"
    assert out_con["quality_gate"] == "FAIL"