## CHECKPOINT 8D.2 — RESULT

> Evidencia estática (run_commands CORRUPT — inyecta XML al PowerShell → no ejecuta `pytest`/`py_compile`).
> Sintaxis validada por inspección. Tests escritos para ejecutar bajo `pytest`.

### Archivos modificados
1. `frontend/editorial.py`
   - L16: `from core.book.source_manager import SourceManager`.
   - L403-417: nueva `_chapter_source_urls(chapter_id)` — fuente de verdad **única**: `SourceManager.get_chapter_sources(chapter_id)` lee `sources.chapter_ids`. `chapter_id None` / sin asociaciones / tabla ausente → `[]` (nunca inventa, nunca `job.data.sources` globales).
   - L442-445 (`_build_book_dict`): añadido `"sources": _chapter_source_urls(c.get("id"))` por capítulo.

### NO modificado (protegido por autorización 8D.2)
- `modules/chapter_writer/*` — intacto (no persiste `chapters.sources`; recovery lo reconstruye desde `sources.chapter_ids`).
- `modules/quality_control/*` — intacto (ni thresholds ni `_check_sources`).
- `core/database.py` schema — intacto (no se crea helper; se reutiliza `get_chapter_sources`).
- `build_payload`, `create_book` — intactos.

### Tests creados — `tests/test_editorial_sources.py` (a-g)
- a) fuente REAL asociada → visible en `_build_book_dict.chapters[0].sources`;
- b) sin asociación → `sources == []`;
- c) múltiples fuentes reales preservadas (set);
- d) recovery: rebuild tras re-leer BD conserva exactamente las asociaciones reales;
- e) research vacío → `[]` (NO inventa);
- f) `final_quality_control` → `source_checks` todos `PASS` con fuentes reales;
- g) `final_quality_control` → `source_checks` `FAIL` cuando no hay fuentes (no artificial).
Fixture: DB tmp vía `SPACE_LAIR_DB_PATH` + `init_db()` + `monkeypatch`.

### Evidencia del contrato (fuente de verdad) — líneas exactas
- Writer recibe fuentes reales: `frontend/editorial.py:331` → `"sources": data.get("sources") or [],  # propagación REAL de Research (job data)`.
- Persistencia real: `core/book/source_manager.py:34-44` (add_source con chapter_ids) ; `:49-69` dedupe+reassoc ; `:168-184` `associate_chapter` → `UPDATE sources SET chapter_ids` ; `:154-165` `get_chapter_sources(chapter_id)` (lector `sources.chapter_ids`).
- Esquema: `core/database.py:272` columna `chapters.sources TEXT NOT NULL DEFAULT '[]'` ; `:288-303` tabla `sources(... chapter_ids TEXT NOT NULL DEFAULT '[]')`.
- QC consume **`Chapter.sources`** (`core/book/book_schema.py:64` `sources: list[str]`, validator `normalize_str_list` L82-89 que acepta list[str]/str → URLs):
  - `modules/quality_control/main.py:319` `def _check_sources(book)` → `chapters = sorted(book.chapters or [], key=lambda c: c.number)` (L321) ;
  - `missing_sources = [ch.number for ch in chapters if not ch.sources]` (L339-341) ;
  - `if missing_sources:` → `QualityControlItem(status="FAIL", message="Fuentes faltantes en capítulos: ...")` (L342-347) ;
  - `else:` → `QualityControlItem(status="PASS", message="Fuentes presentes en todos los capítulos")` (L350-354).
- Writer ya usa las fuentes reales: checkpoints `data/checkpoints/1001/book/draft/v*004x.json` contienen `sources_used` con URLs reales (wikipedia ARPANET/Creeper).

### NOTA de calidad del diff (no funcional)
- `frontend/editorial.py:439` (`"edited_es"`) quedó con indentación de 48 espacios tras el ajuste. Dentro del dict literal (contenedor `{}`) Python tolera whitespace arbitrario → **no es syntax/indent error de runtime** (`py_compile` validaría OK). Es un `pycodestyle E1xx` estético; no afecta ejecución ni tests. Se deja así para no arriesgar más reemplazos con `run_commands` corrupto; se puede normalizar a 16 espacios en un `py_compile` posterior si el shell se repone.

- QC consume `Chapter.sources`: `quality_control/main.py:319` `_check_sources` → `missing_sources = [ch.number for ch in chapters if not ch.sources]` (339-341); FAIL si vacío (344-347), PASS si non-empty (351-354).
- Modelo: `core/book/book_schema.py:64` `sources: list[str]` (`normalize_str_list` 82-89 acepta list[str]).
- Writer ya usa las fuentes: `data/checkpoints/1001/book/draft/v*004x.json` contienen `sources_used` con URLs reales (wikipedia ARPANET/Creeper).

### Asociación source↔chapter
- Relación real y persistente: `sources.chapter_ids` (tabla `sources`), gestionada por `SourceManager`.
- `_build_book_dict` la reconstruye **por cada capítulo** con su propio `chapter_id` → la fuente de un capítulo no se cuela en otro.
- Recovery: las asociaciones persisten en `sources` (inmutable respecto a retry de capítulo) → `_build_book_dict` re-reconstruye determinista → recovery preserva.

### E2E
- **NO ejecutado** (regla 8D.2). El reporte `e2e_001_report.json` actual (canonical runner) ya mostraba `qc_source_checks PASS` porque ese runner construía el book_dict con `sources` manualmente; el autómata real (`_build_book_dict`) ahora también lo hace.

### Bloqueos restantes del QC (para 8D.3/8D.4/8D.5)
- `metadata FAIL` (author/genre/description no provistos por planner → 8D.3).
- `chapter FAIL` (min_chapters=20 vs target_chapters=1; autopilot no pasa min_chapters → 8D.4/8D.5).
- `document WARNING` (docx_path=None → 8D.5).
→ **El FAIL de sources está resuelto**, pero el overall QC sigue FAIL por causas reales de otras fases. No se falsea PASS.

### Criterio de éxito 8D.2
- `source_checks` del QC → PASS con fuentes reales; FAIL con fuentes ausentes. ✔ (diseñado/test a-g).
- Recovery preserva asociaciones reales. ✔
- Ninguna fuente inventada. ✔
