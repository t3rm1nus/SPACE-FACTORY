# CHECKPOINT 8D.2 — SOURCES (DIAGNÓSTICO)

> Método: `read_files` + `search_codebase` (shell/rochestrator NO ejecutable: `run_commands`
> inyecta XML al PowerShell y no valida; por tanto `pytest`/`verify` no se pueden correr en vivo).
> Estado: **diagnosticado — pendiente de aprobación para tocar módulo PROTECTED**.

## 1. ¿Recibe el writer las fuentes reales? → SÍ
- `frontend/editorial.py:315-336` (case `"writer"`): `"sources": data.get("sources") or [],  # propagación REAL de Research (job data)`.
- Research las devuelve: `e2e_001_report.json` `"sources_count": 9` y bloque `sources[]` (url/title reales).
- Evidencia de que el writer las **usa**: `modules/chapter_writer/main.py:1358` incluye `"sources_used"` en el metadata del checkpoint → los checkpoints `data/checkpoints/1001/book/draft/v*004x.json` listan `"sources_used": ["https://es.wikipedia.org/wiki/ARPANET", "https://es.wikipedia.org/wiki/Creeper_(virus)"]`.
- → (4a) **NO es la causa**: el writer recibe fuentes reales (no inventadas).

## 2. ¿Dónde se persisten las fuentes? → SOLO en `sources.chapter_ids`, NUNCA en `chapters.sources`
- `core/book/source_manager.py:34-44` (`add_source`) escribe en tabla `sources` con `chapter_ids` opcional.
- `core/book/source_manager.py:168-184` (`associate_chapter`) hace `UPDATE sources SET chapter_ids = ?` → **escribe `sources.chapter_ids`**, no `chapters`.
- `core/database.py:288-303` define tabla `sources(id, url, url_hash, title, ..., chapter_ids TEXT DEFAULT '[]')`.
- `core/database.py:264-284` define tabla `chapters` con `sources TEXT NOT NULL DEFAULT '[]'` (`database.py:272`) → **existe pero se ignora**.
- No hay helper `update_chapter_sources` (search: 1 hit → mi propia propuesta). → La trazabilidad source↔chapter se mantiene en `sources.chapter_ids`, pero el QC **no la lee**.

## 3. ¿El writer persiste `chapters.sources`? → NO
- `frontend/editorial.py:80`: `_PERSISTABLE_TEXT_FIELDS = ("draft_es","draft_en","edited_es","edited_en")`.
- `frontend/editorial.py:93-94`: `if field not in _PERSISTABLE_TEXT_FIELDS: raise ValueError`.
- `persist_chapter_result` (L83-121) hace `UPDATE chapters SET {field}=?` con field validado → **"sources" es rechazado**. El writer persiste `draft_es` (por `autopilot.py` tras `execute`), pero nunca `sources`.
- `modules/chapter_writer/main.py:1379-1430` (`execute`): `validated = validate_payload(...)` incluye `sources` (`core/schemas.py:120-139` `ChapterWritePayload`); construye el prompt con sources; pero **no llama a nada que escriba `chapters.sources`**.

## 4. ¿`_build_book_dict` propaga sources al book_dict? → NO
- `frontend/editorial.py:402-428` construye `{"chapter_id","book_id","number","title","edited_es","draft_es","images"}` → **NO `"sources"`**.
- `Book/chapter` modelo (`core/book/book_schema.py:54-90`, `Chapter.sources: list[str] = []` L64) → si el dict no incluye `sources`, `model_validate` deja `[]` → **Chapter.sources vacío**.

## 5. ¿Qué exige el QC? → `Chapter.sources` no vacío
- `modules/quality_control/main.py:319-341`:
  ```py
  chapters = sorted(book.chapters or [], key=lambda c: c.number)
  checks = []
  missing_sources = [ch.number for ch in chapters if not ch.sources]   # L339-341
  if missing_sources:
      checks.append(QualityControlItem(status="FAIL", message="Fuentes faltantes en capítulos: ..."))
  ```
- → QC lee **`Chapter.sources`** (modelo), no `sources.chapter_ids`. Confirmado (4c): persistir con estructura/campo incorrecto.

## 6. Recovery
- `_get_chapters` (`editorial.py:46-54`) hace `SELECT * FROM chapters …` → **lee `sources` columna** (existe) → recovery SÍ preservaría `chapters.sources` **si** estuviera poblada.
- El checkpoint (`modules/checkpoint.py`) guarda `draft` + metadata con `sources_used` (writer L1358) → recovery vía checkpoint **parcialmente conserva**, pero el QC consume el **book_dict desde BD** (`_build_book_dict`), donde `chapters.sources='[]'`.

## 7. Research vacío → NO PASS artificial
- `frontend/editorial.py:281-285` writer case: `"sources": data.get("sources") or []` → si `sources=[]`, writer payload `sources=[]` → `Chapter.sources=[]` → `_check_sources` FAIL. Correcto, no hay short-circuit.

## 8. Root cause exacta (4b + 4c)
1. Writer persiste `draft_es` vía `persist_chapter_result`, **pero no persiste `sources`** en `chapters.sources` (rechazado por `_PERSISTABLE_TEXT_FIELDS`).
2. `_build_book_dict` no incluye `"sources"` → book_dict pasa `Chapter.sources=[]` → QC marca FAIL.
3. La columna `chapters.sources` existe y `sources.chapter_ids` ya enlaza, pero **el QC no la consume**: necesita `chapters.sources` populado.

## 9. Cambios mínimos (SIN implementar)
1. `core/database.py` — helper `update_chapter_sources(chapter_id:int, sources:list[str]) -> None` (`UPDATE chapters SET sources=? WHERE id=?`).
2. `modules/chapter_writer/main.py` (**PROTECTED**, `tools/dev/config.py:36` / `PROJECT_STATUS.md:35`) — tras generar `draft_es`/`draft_en`, persistir `json.dumps([s["url"] for s in sources])` en `chapters.sources` vía el helper. → **REQUIERE aprobación explícita (regla 8D.2 + regla 4 PROTECTED_FILES)**.
3. `frontend/editorial.py:423` (`_build_book_dict`) — añadir `"sources": _coerce_sources(c.get("sources"))` al dict del capítulo.

## 10. Criterio de éxito 8D.2
- Test unitario (`tests/test_sources_persistence.py`): `create_book` → `writer` con sources reales → `chapters.sources` populado → `_build_book_dict` incluye `sources` → `final_quality_control` → `source_checks` todos PASS.
- Test: writer con `sources=[]` → `chapters.sources="[]"` → QC `source_checks` FAIL (nada artificial).
- Test: recovery de chapter mantiene `sources`.
- `run_editorial_e2e_001.py`... NO se ejecuta todavía (regla 8D.2: "NO ejecutes todavía el E2E completo").

## 11. Riesgos
- R5.2 `persist_chapter_result` valida `field` → usar helper dedicado (no ensuciar `_PERSISTABLE`).
- R5.5 `modules/chapter_writer/main.py` PROTECTED → bloqueador de approval.
- No modificar `_check_sources` (regla 8D.2: "NO modificar Quality Gate").
