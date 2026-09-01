# PROJECT MASTER STATUS — SPACE LAIR / LIVING AI FACTORY

REPOSITORIO: https://github.com/t3rm1nus/SPACE-FACTORY

> **Manual operativo para IAs.**
> Fuente de verdad: **código actual > tests actuales > E2E > este documento > histórico**.
> Este archivo debe permitir empezar a trabajar sin reconstruir la historia del proyecto.

---

# 1. PRODUCTO

**Space Lair — Living AI Factory**

Plataforma Python/Flask + SQLite + scheduler + módulos especializados + frontend HTML/CSS/JS
para generar libros mediante IA y producir:

`output/docx/book_{book_id}_{language}.docx`

PDF = OUT OF SCOPE / deuda.

Pipeline real (`core/autopilot.py::AUTOPILOT_PHASES`):

```text
planner → research → outline → writer → fact_check → editor
→ image_plan → image_gen → quality_gate → docx
```

Estado global conocido: `KNOWN_GOOD`. No hay blocker global activo.

---

# 2. PRIORIDAD ACTUAL

## `modules/image_search/main.py`

Objetivo: mejorar la búsqueda web de imágenes para obtener candidatos relevantes y de
calidad, manteniendo la búsqueda determinista y sin compensación IA cuando el usuario
elige `image_search_ratio=1.0`.

### Estado conocido

- Selector actual: **first-fit**, sin ranking global.
- La query no usa `chapter_text` real.
- Ya existen filtros de dominio/extensión/hash/anclaje.
- No existe todavía un check suficiente de resolución/aspect ratio.
- Retry SearXNG existente.
- Configuración:
  - `SEARXNG_PER_PAGE=20`
  - `DOWNLOAD_TIMEOUT=20s`
  - `IMAGE_SEARCH_TOTAL_TIME_BUDGET=90s`
  - `IMAGE_SEARCH_MAX_PAGES=6`

### Plan

**Fase 1 — CERRADA:** query enriquecida con keywords salientes de
`chapter_text` (determinista, sin LLM) + quality check Pillow
(dimensiones mínimas + aspect ratio).

**Fase 2 — CERRADA:** ranking de candidatos (pool + best-first),
abandonado first-fit. TODO pendiente sin implementar: penalización
por dominio repetido cross-chapter (no viable sin refactor del hash
registry, que hoy es post-descarga por contenido, no por dominio).

**Fase 3 — CERRADA:** resiliencia SearXNG — backoff exponencial+jitter
en 429 (`SEARXNG_MAX_RETRIES` env-overridable), retry simple en
timeout, fail-fast sin bucle en error de conexión. Señal
`rate_limited` propagada vía campo error existente (aditivo, sin
tocar autopilot.py ni el contrato de retorno).

**Fase 4 — CERRADA:** verificación semántica VLM (moondream-local vía
GGUF importado manualmente, Ollama no soporta el modelo en su registro
oficial por bloqueo de red a Cloudflare R2 — ver nota de
infraestructura abajo) sobre el candidato ya seleccionado por el
ranking de Fase 2. Flag `VLM_VERIFICATION_ENABLED` (default 0,
activación explícita). Fail-open ante timeout/error/respuesta
ambigua. Validado con datos reales (book_85, 17/17 SI, 0 fail-open,
overhead ~+2.5s/imagen, budget respetado con holgura >3x).

**PLAN COMPLETO — CERRADO.** Activación en producción pendiente de
decisión del arquitecto (flag sigue en 0 por defecto).

### Regla

La mejora de `image_search` está **autorizada**. Puede rehacerse limpiamente si eso
reduce complejidad, pero no ampliar el alcance a otros módulos sin necesidad demostrada.

### Restricción operativa — Ollama single-process

Confirmado (diagnóstico de arquitectura): el pipeline es serial de punta
a punta (worker único, FIFO estricto en scheduler.py y autopilot.py) —
esto es lo que hace seguro usar VLM sin lock explícito para Ollama. Si
en el futuro se ejecuta más de UN proceso `run.py web` simultáneo (ej.
escalado horizontal, reloader duplicado), cada proceso tendría su propio
worker singleton y podría haber contención real de VRAM entre
chapter_writer y VLM de procesos distintos. No añadir paralelismo de
procesos sin revisar esto primero.

---

# 3. ARQUITECTURA MÍNIMA

```text
core/
  database.py       SQLite/migraciones/WAL
  task_queue.py     cola SQLite
  scheduler.py      ejecución/retry/reaper
  autopilot.py      pipeline
  schemas.py        contratos de payload
  providers/        LLM
  image_providers/  imágenes IA

modules/
  book_planner/
  research/
  chapter_writer/
  fact_checker/
  editor/
  image_planner/
  image_generator/
  image_search/
  quality_control/
  document_builder/

frontend/
  index.html
  app.js
  style.css
  frontend_api.py
  editorial.py

tests/
tools/
data/
output/
```

Responsabilidades críticas:

| Pieza | Función |
|---|---|
| `autopilot.py` | orquesta fases |
| `scheduler.py` | procesa tasks |
| `task_queue.py` / `database.py` | persistencia |
| `modules/*` | capacidades |
| `frontend_api.py` | REST + SSE |
| `document_builder` | DOCX |
| `quality_control` | gates |

---

# 4. DATOS IMPORTANTES

## `books`

Campos relevantes:
`title`, `subtitle`, `description`, `author`, `genre`, `target_audience`,
`target_chapters`, `image_count`, `image_search_ratio`, `layout_config`, `languages`,
`status`.

`image_search_ratio`:
- `0.0` = solo IA
- `1.0` = solo web
- intermedio = split

## `chapters`

Campos relevantes:
`title`, `title_en`, `outline`, `outline_en`, `draft_es`, `draft_en`,
`edited_es`, `edited_en`, `images`, `sources`, `quality_status`.

Las fuentes se mantienen mediante `SourceManager`.

## Persistencia

- imágenes: `chapters.images`;
- checkpoints: `data/checkpoints/`;
- tareas: SQLite;
- DOCX: `output/docx/`.

---

# 5. COMPORTAMIENTO DE COMPONENTES

## Writer

`modules/chapter_writer/main.py`

**PROTECTED.**

- LLM local con timeout/retry limitado.
- fallback determinista.
- backstop para mínimo de palabras.
- variantes nativas `_es` / `_en`.
- objetivo: ≥1500 palabras, sin placeholders ni duplicación evidente.

Deuda abierta:
el backstop puede solaparse con texto previo del LLM tras continuaciones rechazadas.

No tocar salvo autorización explícita.

## Editor

`modules/editor/main.py`

Fallback determinista cuando la salida LLM es inválida/inútil.

## Fact check / QC

Las severidades están diferenciadas.
La fabricación estructural puede bloquear.
Accuracy parcial no necesariamente tumba el job.

No rebajar gates para conseguir PASS.

## Document Builder

`modules/document_builder/main.py`

Genera portada, legal, TOC, introducción, capítulos e imágenes.

Características importantes:
- filename `book_{book_id}_{language}.docx`;
- fuentes reconstruidas desde BD;
- verificación post-`doc.save()`;
- A4 por defecto;
- links reales;
- WEBP convertido cuando procede.

---

# 6. IMÁGENES

## IA

ComfyUI + SDXL Base/Refiner = proveedor por defecto.

Fallback:
`LocalImageProvider`.

## Web

SearXNG mediante `modules/image_search`.

## Persistencia/dedupe

- merge por `image_path`;
- dedupe cross-chapter por hash SHA-1;
- inserción DOCX con caption y numeración.

---

# 7. FRONTEND

Archivos:
`frontend/index.html`, `app.js`, `style.css`, `frontend_api.py`, `editorial.py`.

El frontend representa **estado real**, sin mock data.

Vistas:
- SALA DE CONTROL
- LIBROS
- ACTIVIDAD
- SISTEMA

Pipeline visible:
10 fases de `AUTOPILOT_PHASES`.

SSE:
`/api/stream`

Endpoints principales:
- `POST /api/books`
- `GET /api/books`
- `GET /api/books/<id>`
- `GET/POST /api/books/<id>/autopilot`
- `GET /api/books/<id>/docx`
- `DELETE /api/books/<id>`
- `DELETE /api/books`
- `GET /api/tasks`
- `GET /api/modules`
- `GET /api/metrics`

`/api/books` ya fue optimizado usando `json_extract` para evitar transferir payloads gigantes.

---

# 8. BASE DE DATOS / RECUPERACIÓN

SQLite:
`data/space_lair.db`

Características actuales:
- WAL;
- `busy_timeout=30000`;
- migraciones automáticas.

Task states:
`pending | running | done | error | pending_approval | cancelled`

El scheduler tiene reaper para tasks `running` huérfanas.

Deuda residual:
las fases del job aún pueden requerir cold-start para recuperación completa.

---

# 9. TESTING

Último checkpoint de suite completa conocido:

```text
809 passed, 0 failed, 0 errors, 1 skipped
2026-08-29
```

Después hubo fixes focalizados posteriores que no están incluidos en ese checkpoint.

### Política de validación

**No ejecutar la suite completa de rutina.**

```text
cambio local
  → test focalizado

cambio de módulo
  → tests del módulo

cambio de orquestación
  → tests focalizados + E2E

checkpoint de integración
  → suite completa
```

No declarar PASS sin la validación apropiada.

---

# 10. PROTOCOLO FAST PATH PARA IAs

## Regla principal

**No repetir comprobaciones que no aportan información nueva.**

Aceptar el estado de este documento como contexto inicial salvo que:
- el usuario pida auditoría;
- exista evidencia de regresión;
- el cambio contradiga el estado documentado;
- haya una razón concreta para sospechar que el estado está obsoleto.

## Flujo normal

```text
1. Leer este documento.
2. Ir al archivo relevante.
3. Localizar función/clase responsable.
4. Confirmar solo la premisa necesaria.
5. Modificar.
6. Ejecutar la prueba mínima útil.
7. Escalar validación solo si aumenta el riesgo.
8. Actualizar este documento solo si cambia el estado operativo.
```

## Prohibido por defecto

- releer el changelog histórico;
- reauditar todo el pipeline;
- ejecutar tests no relacionados;
- hacer E2E para cambios puramente locales;
- lanzar la suite completa por un bug aislado;
- repetir una autorización ya vigente;
- refactorizar código no relacionado;
- tocar código sano sin evidencia;
- cambiar tests para forzar PASS.

## Regla de proporcionalidad

```text
helper local          → unit test
módulo                → tests del módulo
orquestación          → tests + E2E
cambio transversal    → suite completa
```

El objetivo es gastar tokens en **implementar, depurar y generar trabajo**, no en demostrar
repetidamente que lo que no se ha tocado sigue funcionando.

---

# 11. PROTOCOLO DE DIAGNÓSTICO

Cuando hay un fallo:

```text
fallo
 ↓
localizar fase/función
 ↓
leer código implicado
 ↓
causa raíz
 ↓
fix mínimo
 ↓
test que reproduce/cubre
 ↓
escalar solo si procede
```

Cuando no hay evidencia de fallo:
**no inventar una investigación preventiva.**

Cuando documento y código difieren:
**manda el código.**

Cuando tests y código difieren:
determinar el contrato vigente; nunca modificar tests para silenciar una regresión.

---

# 12. ESTADOS

Usar solo cuando aporten información:

`IMPLEMENTED`
`VALIDATED`
`IMPLEMENTED + VALIDATED`
`PARTIAL`
`NOT_IMPLEMENTED`
`KNOWN_ISSUE`
`BLOCKED`
`DEBT`
`OUT_OF_SCOPE`
`UNKNOWN`

No confundir implementación con validación.

---

# 13. PROTECCIONES Y AUTORIZACIONES

Fuente:
`tools/dev/config.py`
`tools/dev/security.py`

## PROTECTED

- `modules/chapter_writer/main.py`
- `tests/`

## OUT_OF_SCOPE

**Cambio de gobernanza (2026-09-01, decisión explícita de robustiano):**
la autorización para módulos OUT_OF_SCOPE puede darla robustiano (operador)
o Claude (arquitecto), sin requerir a Paquito de forma obligatoria. Paquito
conserva autoridad de colaborador pero deja de ser bloqueante por defecto.

Requieren autorización específica (robustiano o Claude):
- `research`
- `fact_checker`
- `editor`
- `document_builder`
- `quality_control`
- `pdf_builder`
- `image_generator`
- `image_planner`
- `book_planner`
- `translator`
- `text_summarizer`
- `word_counter`
- `mcp_demo`
- `mcp_external`
- `image_search`

## LIBRE

`tools/`

Además, frontend, `run.py`, E2E runner e infraestructura de datos son modificables con
cuidado.

### Autorización vigente

La mejora de `modules/image_search` está autorizada y puede incluir reescritura limpia
del módulo si es necesaria para el diseño.

No extrapolar esa autorización a otros módulos.

---

# 14. LIMITACIONES Y DEUDA ACTUAL

| Tema | Estado |
|---|---|
| ComfyUI necesita servidor/checkpoints | LIMITATION |
| Sin Ollama se pierde calidad LLM donde haya fallback | LIMITATION |
| Worker/server debe estar activo | LIMITATION |
| PDF | OUT_OF_SCOPE |
| Selector de proveedor de imagen en front | BACKLOG |
| Penalización dominio repetido en scoring | DEBT (requiere refactor de hash registry) |
| Restricción operativa | Ollama single-process asumido por el diseño actual — no paralelizar sin revisión (ver §2) |
| Lists Markdown como listas nativas Word | LIMITATION |
| Reaper completo de fases de job | DEBT |
| Suite completa posterior a fixes recientes | DEBT DE VALIDACIÓN |
| Línea 437 book_planner/main.py: rango "20-40 capítulos" hardcodeado en prompt LLM, no usa target_chapters real | DEBT (colateral, no bloqueante, sin autorización de fix aún) |

---

# 15. ROADMAP

## AHORA

```text
image_search Fase 1
  ├─ query enriquecida desde chapter_text
  └─ quality checks Pillow
        ↓
Fase 2
  └─ re-ranking
        ↓
Fase 3
  └─ resiliencia
        ↓
Fase 4
  └─ VLM
```

## DESPUÉS

1. Suite completa de integración.
2. Confirmar ausencia de regresiones.
3. Volver al backlog secundario.

## FUTURO

- PDF estable.
- selector de proveedor/modelo de imagen.
- portada KDP.

---

# 16. CHECKPOINT ACTUAL

```text
PROJECT:                 KNOWN_GOOD
BLOCKERS:                NONE

PIPELINE:                10 fases implementadas
WRITER:                  IMPLEMENTED + VALIDATED
EDITOR:                  IMPLEMENTED + VALIDATED
RESEARCH:                IMPLEMENTED + VALIDATED
BOOK PLANNER:            IMPLEMENTED + VALIDATED
DOCUMENT BUILDER:        IMPLEMENTED + VALIDATED
EN GENERATION:           IMPLEMENTED + VALIDATED
LAYOUT:                  IMPLEMENTED + VALIDATED
FRONTEND:                IMPLEMENTED

IMAGE ORCHESTRATION:     IMPLEMENTED
IMAGE PERSISTENCE:       IMPLEMENTED
COMFYUI:                 DEFAULT
SEARXNG:                 ACTIVE
IMAGE SEARCH:            FASES 1-4 IMPLEMENTED + VALIDATED. Plan completo
                         cerrado. VLM_VERIFICATION_ENABLED=1 — ACTIVADO EN
                         PRODUCCIÓN (decisión explícita del arquitecto,
                         2026-09-01). Validación en curso: libro completo
                         generado manualmente desde el frontend por el
                         arquitecto, pendiente de confirmar resultado real.
                         VLM persistence + API + frontend exposure:
                         IMPLEMENTED + VALIDATED (2026-09-01).

FULL SUITE:              809 passed / 0 failed / 0 errors / 1 skipped
FULL-SUITE DATE:         2026-08-29
POSTERIOR FIXES:         validated de forma focalizada

E2E:                     known real editorial E2E PASS
DOCX:                    PASS
PDF:                     OUT OF SCOPE

CURRENT TASK:            PENDIENTE: resultado de validación manual VLM en
                         producción (libro en curso desde frontend). No
                         tocar image_search hasta confirmación.
NEXT TASK:               (sin definir)
```

---

# 17. CRITERIO DE CIERRE DE FASE 1

Fase 1 termina cuando:

- `chapter_text` influye realmente en la query;
- la extracción de keywords es determinista;
- se conserva el contexto actual de libro/capítulo;
- Pillow rechaza imágenes de calidad insuficiente;
- un candidato rechazado no aborta innecesariamente toda la búsqueda;
- tests del módulo cubren los caminos nuevos;
- no se rompe la capability/API existente;
- el resultado queda listo para que Fase 2 reutilice las keywords.

**No hace falta para cerrar Fase 1:**
- reauditar el pipeline;
- repetir bugs históricos;
- ejecutar la suite completa;
- modificar módulos no relacionados.

---

# 18. HANDOFF

```text
You are entering an existing production-like codebase.

START:
1. Read this file.
2. Current default task = image_search Fase 1.
3. Inspect modules/image_search/main.py and its relevant tests.
4. Change only what is necessary.
5. Run focused tests.
6. Use E2E only when the change crosses the pipeline boundary.
7. Do not run the full suite unless this is an integration checkpoint.
8. Update this file only when operational state changes.

DO NOT:
- reconstruct historical bugs;
- revalidate unrelated modules;
- modify tests to force PASS;
- touch chapter_writer without authorization;
- broaden scope without evidence;
- add AI compensation for ratio=1.0.

PRIORITY:
image_search
  Fase 1 → query enrichment + quality checks
  Fase 2 → re-ranking
  Fase 3 → resilience
  Fase 4 → visual verification
```

---

# 19. PRINCIPIO OPERATIVO

> **NO COMPROBAR POR COMPROBAR.**
>
> El documento existe para reducir contexto repetido.
> La validación debe ser proporcional al riesgo.
> Los tokens deben concentrarse en **hacer el trabajo, detectar errores reales y avanzar**.

---

# CHANGELOG — image_search FASES 1-3 / 2026-08-31

query enriquecida (keywords deterministas) + quality check Pillow + ranking
best-first (abandona first-fit) + resiliencia SearXNG (backoff 429, distinción
`rate_limited` vs `no_results`). 38/38 tests módulo, 0 regresiones acumuladas
desde Fase 1. autopilot.py no tocado en ninguna de las 3 fases. Fase 4 (VLM)
pendiente de decisión de arquitectura.

---

# CHANGELOG — IMAGE_SEARCH FASE 4 (VLM) / 2026-08-31

verificación semántica con moondream-local (GGUF importado manualmente vía
Hugging Face, registro oficial de Ollama bloqueado por red — Cloudflare R2
inaccesible desde este entorno). Flag `VLM_VERIFICATION_ENABLED` default 0.
Validado real en book_85: 17/17 SI, 0 fail-open, overhead ~+2.5s/imagen,
budget respetado (peor caso 32% de 90s). Plan image_search Fases 1-4 CERRADO.
Nota de arquitectura: pipeline single-process/serial confirmado como lo que
hace seguro este diseño sin lock explícito para Ollama — restricción
documentada en §2/§14 para no romperla al escalar.

---

# CHANGELOG — VLM PERSISTENCE + API EXPOSURE / 2026-09-01

Trazabilidad VLM en front: `vlm_checked`/`vlm_candidates_tried` añadidos a
`ImageMetadata` (core/schemas.py, Optional default None — contrato histórico
intacto) y persistidos en modules/image_search/main.py tras aceptación de
candidato (flag activo → vlm_checked=True; 1 = aceptado a la primera).
`api_book_detail` (frontend/frontend_api.py) ahora expone chapters[].images
como lista de objetos reales (antes solo image_count), vía helper
`_resolve_image_entry` que resuelve cada ruta a su <image_id>.metadata.json
(en huérfanas devuelve {"path": ...} sin inventar campos). Validado: 42/42
tests módulo image_search + assert nuevo de persistencia VLM en
test_vlm_enabled_si_acepta_candidato; respuesta real de GET /api/books/84
confirmada con estructura nueva. RETRACTADO: un reporte de sesión anterior
afirmó un ciclo E2E completo sobre "book_89" (19/21 VLM, DOCX PASS) — book_89
no existe en BD, esa claim queda sin evidencia y no debe tratarse como
validada. PENDIENTE: validación E2E real de vlm_checked en una imagen NUEVA
(no histórica) generada con el flag activo — candidato sugerido: book_88
(temática ferroviaria), pendiente de decisión del arquitecto sobre cuándo
ejecutarla.

---

# CHANGELOG — VLM FRONTEND EXPOSURE / 2026-09-01

renderCurrentBook (frontend/app.js) ahora pinta imágenes por capítulo con
badge de estado VLM (done/pending/cancelled reusando .cb-status existente),
vía state.currentBookImages cargado desde GET /api/books/<id> en
loadCurrentBookDetail. AUTOPILOT_PHASES y renderLivingPipeline sin tocar
(VLM no es fase propia, vive dentro de image_gen — decisión de diseño
explícita, no omisión). CSS nuevo: 2 reglas de layout puro (.cb-image-line,
.cb-vlm-note), cero color nuevo. Validado con datos reales (book_84, 20
capítulos con imágenes, todas correctamente "VLM: N/D" por ser históricas)
+ 3 casos sintéticos para done/pending/nota-de-rechazo (no reproducibles hoy
en producción porque VLM_VERIFICATION_ENABLED sigue en 0). Corrección de
sesión: afirmación previa de que el endpoint /load no existía era incorrecta
(fallo de grep, no de código) — existe en frontend_api.py:825.

---

# CHANGELOG — QUALITY_GATE DEFICIT POLICY / 2026-09-01

Decisión explícita del arquitecto — déficit de imágenes por capítulo
(count < target) degradado de FAIL a WARNING en _check_images
(modules/quality_control/main.py), sin condicionar a ratio (antes solo
toleraba <=1 con ratio==1.0). Exceso sigue FAIL sin cambios. image_search
no tocado. 5 tests actualizados a la política nueva (renombrados los de
ratio, cuyo condicionamiento quedó derogado; exceso→FAIL y book_76
0-imágenes conservados), suite del módulo 15 passed. book_89 recuperado
vía retry_job (NO reset_from_phase — causa no estaba en image_gen) tras
reiniciar el server para cargar el código nuevo (riesgo §19 P2 proceso
obsoleto: el primer retry re-ejecutó el gate con la política vieja) —
resultado: overall_status=WARNING (déficits ya no bloquean), docx PASS —
ruta: output/docx/book_89_es.docx (y book_89_en.docx, 18:41).

---

# 20. KNOWN_ISSUE — book_planner fallback sistémico (detectado 2026-09-01)

Estado: KNOWN_ISSUE, diagnóstico en curso, SIN FIX.

- 11 books con capítulos título "- Parte N" (fallback genérico):
  76, 77, 78, 80, 82, 84, 85, 86, 87, 88, 89.
- Causa código: modules/book_planner/main.py, función `execute` (línea ~635),
  bloque try/except (651-697) cae a `_fallback_plan` ante CUALQUIERA de:
  provider ausente, error del LLM, JSON no extraíble, o
  `len(chapters) < target_chapters`. El motivo real no se persiste en BD
  (solo logger.debug del raw_text, no guardado).
- book_89 específico: 3 tasks de planner (2859, 2860, 2869) para el mismo
  book_id. El books.title final ("Historia de los videojuegos") corresponde
  a la task 2869, pero los 24 `chapters` persistidos corresponden a la
  task 2859/2860 (idea distinta: "Videojuegos, desde el pong hasta el GTA 6").
  Desajuste sin explicar todavía — posible bug de orquestación
  (task_queue/scheduler), no necesariamente de book_planner.
- Efecto secundario en image_search: el título de fallback de book_89
  contiene literalmente "GTA 6", que se usó como query de imagen en los 24
  capítulos (chapter_search_topic/topic_en vacíos) — explica la
  concentración de imágenes de GTA. image_search NO tiene bug aquí; recibió
  una query ya contaminada.
- book_planner es OUT_OF_SCOPE — cualquier fix de código ahí requiere
  autorización de Paquito. La duplicación de tasks (task_queue/scheduler)
  NO está en OUT_OF_SCOPE — pendiente de diagnóstico separado.
- NEXT: diagnóstico de logs de servidor (causa exacta del fallo LLM) +
  diagnóstico de por qué se generan tasks de planner duplicadas.

---
DIAGNÓSTICO 2026-09-01 (cierre de vía histórica):
- Logging real: core/logger.py:71, StreamHandler a stdout únicamente,
  sin archivo propio, nivel raíz = LOG_LEVEL env (INFO en producción).
- No hay ningún log en disco que cubra el 01/09 (tasks 2859/2860/2869,
  ventana 11:47-11:49). Los logs más recientes en disco son del 22-29/08.
- El logger.debug(raw_text) del fallback (línea ~692) es nivel DEBUG:
  aunque hubiera existido captura a archivo, con LOG_LEVEL=INFO nunca
  se habría escrito.
- CONCLUSIÓN: causa raíz de book_89 NO recuperable retroactivamente.
  Vía histórica CERRADA. Pivote a fix prospectivo (ver PASO 2 más abajo
  / próximo changelog).
