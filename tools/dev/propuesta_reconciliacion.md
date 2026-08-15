# Reconciliación diagnóstico 7.9D.7 — CHECKPOINT → acción

> Registro WHY/WHAT/FILES/VERIFICATION/RESULT (protocolo AGENTS.md §5).
> Herramientas fiables usadas: `read_files` + `search_codebase` (run_commands reportado CORRUPT: inyecta XML literal al PS, no ejecuta).

## Estado real vs state.json (desincronizado)
- **E2E canónico** (`config.E2E_CMD` = `run_e2e_001_editorial.py`, `tools/dev/config.py:23`):
  `e2e_001_report.json` actual = `status: completed`, `qc_overall_status: PASS`,
  `chapter_word_count: 1834`, `docx_status: PASS`, `qc_book_checks/metadatos/sources/images/document = PASS`.
  → runner manual YA supera el QC (aplica `CHAP_FORCE_MIN=1` + backstop determinista + overrides manuales de payload).
- `state.json`/`PROJECT_STATUS.md` (2026-08-12): `E2E_STATUS=FAIL`, `FAILED_STAGE=chapter`,
  `ROOT_CAUSE: "E2E 001 report aún apunta al informe viejo con 758 palabras; capítulo determinista validado"`.
  → **STALE**: no refleja el reporte actual. Violación del protocolo ("fuente única de estado").

## Root-cause confirmada (autómata REAL — core/autopilot.py — vs runner manual)
| # | Problema | Causa raíz (archivo:línea) |
|---|----------|-----------------------------|
| 1 | metadata FAIL | `book_planner.execute` NO devuelve `author`/`genre`/`language` (`modules/book_planner/main.py:314-325`); `editorial.create_book` INSERTa NULL → `modules/quality_control/main.py:93-101` (`_check_book` exige title/author/description/genre/target_audience). |
| 2 | chapters FAIL | `build_phase_payload` autómata reusa `build_payload("docx")` → no pasa `min_chapters` (`core/autopilot.py:510-513`); default 20 (`core/schemas.py:458`) vs 1 capítulo planeado (`run_e2e_001_editorial.py:65` PLAN_PAYLOAD target_chapters=1). |
| 3 | sources FAIL | `_build_book_dict` no incluye `"sources"` (`frontend/editorial.py:415-427`); writer no persiste en `chapters.sources` (`persist_chapter_result` solo `_PERSISTABLE_TEXT_FIELDS` draft/edit, L80); col. `chapters.sources` existe (`core/database.py:272`) pero queda `'[]'`. QC: `_check_sources` L319-341 (`missing_sources = [ch.number for ch in chapters if not ch.sources]`). NOTA: `source_ids` NO EXISTE (0 hits) — summary CHECKPOINT lo postuló inventado. |
| 4 | document WARNING | autómata no pasa `docx_path` al gate → `QualityControlOutput` default None → `_check_documents` (L493-622) WARNING. Caso A (normal: doc builder no corrió todavía). |

## Cambio #1 propuesto (prioridad PROB 3 — sources)
WHAT:
- A) `frontend/editorial.py:415-427` — en `_build_book_dict`, incluir `"sources": _loads_list(c.get("sources") or "[]")` en el dict de capítulo. (lector BD → modelo `Chapter.sources`.)
- B) `modules/chapter_writer/main.py` (PROTECTED `config.py:36` + `PROJECT_STATUS.md:35`) — tras escribir, persistir `json.dumps(sources)` en `chapters.sources` vía `persist_chapter_result`/`UPDATE chapters` (columna ya existe `database.py:272`).
- C) `core/database.py` — helper `update_chapter_sources(chapter_id, sources)` (UPDATE chapters SET sources=? WHERE id=?).
WHY: cierra el puente Research→Writer→BD→QG; el puente ya existe Research→Writer/FactCheck (propagación OK), falta persistencia capítulo.
FILES: editorial.py, modules/chapter_writer/main.py, database.py.
VERIFICATION: re-ejecutar `run_e2e_001_editorial.py` (canonical) → `qc_source_checks` PASS en autómata; 86 tests pytest siguen PASS.
RESULT: (a proponer) — requiere approval x PROTECTED.

## Sync estado (prioridad protocolo AGENTS.md §2)
- Actualizar `state.json`/`PROJECT_STATUS.md`: `E2E_STATUS=PASS`, `FAILED_STAGE=null`, `ROOT_CAUSE=""`, `NEXT_ACTION="mantener green; alinear autómata real a los overrides del runner"`. — NO en ALLOWED_AUTO_EDIT_DIRS (tools/) → requiere approval.

## Nota sobre regla 8 / 9
- Regla 8 prohíbe tocar `min_chapters`/thresholds/planner → PROB 2 NO se fuerza a 20; el FIX real es alinear `min_chapters` al `target_chapters` del plan (1) en `build_phase_payload`, no bajar el mínimo.
- Regla 9 (phase 7.9D.7) es foco `chapter_writer/main.py` ≥1500 palabras sin placeholders/duplicados — YA validado (e2e report: 1834, `quality_errors` residuales de "duplicación potencial" — revisar si exigen 0 en vez de PASS overall).
