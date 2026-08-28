# PROJECT MASTER STATUS — SPACE LAIR / LIVING AI FACTORY


REPOSITORIO  ---->   https://github.com/t3rm1nus/SPACE-FACTORY

> **Documento maestro humano/IA del proyecto.** Fuente de contexto principal para
> cualquier IA que vaya a trabajar después en este repositorio.
>
> **Regla central:** este documento es un *manual operativo + mapa técnico + estado
> actual + hoja de ruta*. NO es un resumen narrativo promocional.
>
> **Prioridad de la verdad:** código + tests + E2E **>** cualquier documento histórico.
> Si un documento antiguo contradice la realidad del código, manda la realidad.
>
> Fecha de elaboración: 2026-08-15 (a partir de inspección directa del repo).

---

## CÓMO LEER ESTE DOCUMENTO (para una IA nueva)

1. Lee primero este archivo completo.
2. Para cada funcionalidad, distingue siempre:
   - `IMPLEMENTED` (existe en código) versus `VALIDATED` (comprobado por tests/E2E).
   - `IMPLEMENTED + VALIDATED`, `IMPLEMENTED + NOT_YET_VALIDATED`, `PARTIAL`,
     `NOT_IMPLEMENTED`, `KNOWN_ISSUE`, `BLOCKED`, `DEBT`, `OUT_OF_SCOPE`, `UNKNOWN`.
3. Nunca declares PASS sin ejecutar la prueba/E2E correspondiente (protocolo AGENTS.md §5).
4. Código + tests + E2E tienen prioridad sobre estados históricos.

---

# 1. IDENTIDAD DEL PROYECTO

- **Nombre:** Space Lair — Living AI Factory.
- **Propósito / problema que resuelve:** orquestar la **generación editorial asistida
  por IA** de libros completos (desde una idea hasta un documento `.docx` profesional),
  encadenando agentes de IA especializados con un control determinista del flujo.
- **Tipo de aplicación:** plataforma híbrida. **Backend** en Python (Flask + SQLite +
  cola de tareas + scheduler + motores de módulos) y **Frontend** web 8-bit
  (HTML/CSS/JS) como panel de control sobre el backend.
- **Qué produce:** documentos `.docx` (DOCX) de libros formateados (portada, legal,
  índice, introducción, capítulos con imágenes). PDF es **deuda OUT OF SCOPE**.
- **Flujo principal de usuario:** el usuario crea un libro desde el panel web →
  se lanza el **Autopilot** → el pipeline editorial ejecuta fases → se genera el
  DOCX en `output/docx/`.

**Status global (comprobado):** `KNOWN_GOOD` — pipeline editorial→document_builder→docx
sano y verde (ver secciones 14-16).

---

# 2. OBJETIVO FINAL DEL PRODUCTO

Cuando esté terminado, el sistema debería poder, de forma autónoma y **determinista**:

```text
Usuario
  ↓
Nuevo proyecto editorial (título, idea, autor, género, nº capítulos, imágenes, maquetación)
  ↓
Book Planner (plan editorial: capítulos + requisitos)
  ↓
Research (búsqueda web real → fuentes verificadas)
  ↓
Outline (esquema / secciones por capítulo)
  ↓
Chapter Writer (redacción del capítulo, ≥1500 palabras sin placeholders ni duplicados)
  ↓
Fact Check (verificación de afirmaciones contra fuentes)
  ↓
Editor (revisión editorial del capítulo)
  ↓
Image Planning (plan de imágenes por capítulo)
  ↓
Image Generation (generación y persistencia de imágenes)
  ↓
Quality Gate (control de calidad final)
  ↓
Document Builder (generación DOCX)
  ↓
DOCX (output/docx/book_{book_id}_{language}.docx)
```

**NOTA:** El flujo real encontrado en el código del **Autopilot** (`core/autopilot.py`
`AUTOPILOT_PHASES`) incluye las 10 fases de arriba. El **Frontend** (`app.js`) ya las
muestra desde FASE 8F.3 (incluye `image_plan`/`image_gen`, ver §16). El runner E2E 001
(`run_e2e_001_editorial.py`), en cambio, sigue ejecutando efectivamente sin esas 2 fases
porque usa `image_count=0` — esto sigue siendo cierto.

---

# 3. ARQUITECTURA ACTUAL

Estructura real verificada en disco:

```text
SPACE LAIR/
├── core/                  # Núcleo del sistema
│   ├── database.py        # SQLite + migraciones automáticas
│   ├── schemas.py         # Schemas Pydantic de payloads/salidas por capability
│   ├── task_queue.py      # Cola de tareas (CRUD sobre tabla tasks)
│   ├── scheduler.py       # Bucle que procesa tareas (timeouts, retries)
│   ├── module_registry.py # Carga de módulos (module.json + main.py)
│   ├── central_ai.py      # Selección de módulo por capacidad
│   ├── autopilot.py       # ORQUESTADOR del pipeline editorial (jobs/fases)
│   ├── checkpoint.py      # Persistencia de checkpoints por fases
│   ├── workflow.py        # Workflows YAML (steps, paralelo, condiciones)
│   ├── events.py          # Bus de eventos
│   ├── logger.py          # Logging JSON estructurado
│   ├── metrics.py         # Coste y tokens
│   ├── storage.py         # Resultados grandes en disco
│   ├── auth.py            # Tokens JWT (approve/reject)
│   ├── mcp_bridge.py      # Soporte MCP
│   ├── providers/         # Proveedores LLM (ollama, openai_compatible, anthropic)
│   ├── image_providers/   # Proveedores de imágenes (local, comfyui)
│   └── book/              # Modelo de datos editorial (Book/Chapter/Source)
│       ├── book_schema.py
│       ├── book_state.py
│       └── source_manager.py   # Persistencia de fuentes (sources.chapter_ids)
├── modules/               # Módulos (agentes/tools) cargables
│   ├── book_planner/      # create_book_plan
│   ├── research/          # research_web / fetch_url / extract_text (Wikipedia)
│   ├── chapter_writer/    # write_chapter_es / write_chapter_en
│   ├── fact_checker/      # fact_check_chapter
│   ├── editor/            # edit_chapter
│   ├── image_planner/     # create_chapter_image_plan
│   ├── image_generator/   # generate_image / generate_chapter_images
│   ├── image_search/      # search_chapter_images (SearXNG, standalone, NO wired aún)
│   ├── quality_control/   # final_quality_control (QC reglas, sin LLM)
│   ├── document_builder/  # build_book_docx
│   ├── pdf_builder/       # build_book_pdf  (DEUDA OUT OF SCOPE)
│   ├── translator/        # translate_es_en / translate_en_es
│   ├── text_summarizer/   # summarize_text
│   ├── word_counter/      # count_words (tool, sin IA)
│   ├── mcp_demo/          # reverse_text (demo MCP)
│   └── mcp_external/      # external_tool (MCP externo)
├── frontend/              # Panel web 8-bit + API Flask
│   ├── index.html
│   ├── app.js             # Representa estado real del backend (sin mock data)
│   ├── style.css
│   ├── frontend_api.py    # Flask REST + SSE
│   └── editorial.py       # Construye payloads por fase + mapea BD→modelo
├── tests/                 # Tests unitarios (+ E2E runner)
├── tools/                 # Infraestructura de desarrollo
│   ├── orchestrator.py    # CLI del orquestador
│   └── dev/               # config, security, parsers, state, runner, agent_loop, autonomous
├── data/                  # SQLite, checkpoints, artifacts, jobs, imágenes, dev_ops
├── output/                # Entregables (docx/...)
├── run.py                 # CLI (click): demo/serve/web/status/enqueue/...
├── run_e2e_001_editorial.py  # Runner E2E editorial 001
├── requirements.txt       # pydantic, flask, python-dotenv, click, python-docx, fpdf2, pypdf
├── PROJECT_STATUS.md      # Estado persistente (orquestador)
├── AGENTS.md              # Protocolo de desarrollo autónomo
└── PROJECT_MASTER_STATUS.md  # ESTE documento
```

Responsabilidades clave:

| Componente | Responsabilidad |
|---|---|
| **Frontend** | Panel web 8-bit; representa el estado REAL del backend vía API + SSE. Sin mock data. |
| **API (Flask)** | Endpoints REST (`/api/*`) + stream SSE (`/api/stream`). Sirve frontend estático. |
| **Autopilot** | Orquestador del pipeline editorial: jobs, fases, retry, recovery, persistencia de estado en disco. |
| **Scheduler** | Bucle que asigna tareas pendientes a módulos capaces, con timeout y retry. |
| **Task system** | Cola SQLite (`tasks`) + estados (pending/running/done/error/pending_approval/cancelled). |
| **Providers (LLM)** | Ollama (activo), openai_compatible, anthropic (opcional). Desacoplados por `core/providers`. |
| **Database** | SQLite (`data/space_lair.db`), migraciones automáticas. |
| **Modules** | Agentes/tools editoriales cargados por `module_registry`. |
| **Document Builder** | Generación de DOCX profesional (portada, TOC, capítulos, imágenes, presets). |
| **Image system** | Planificación + generación + persistencia de imágenes por capítulo. |
| **Testing** | `tests/` + `modules/*/tests/` + E2E runner. |
| **E2E** | `run_e2e_001_editorial.py` ejecuta el pipeline real y escribe `e2e_001_report.json`. |


---

# 4. PIPELINE EDITORIAL REAL

La fuente de verdad es **`core/autopilot.py` → `AUTOPILOT_PHASES`** (código actual).
Orden real verificado:

| Fase (id) | Capability | Implementada | Validada | Per chapter | Entrada | Salida | Dependencias | Estado |
|---|---|---|---|---|---|---|---|---|
| planner | `create_book_plan` | ✅ | ✅ (E2E) | No | idea/título | plan (capítulos) | — | IMPLEMENTED+VALIDATED |
| research | `research_web` | ✅ | ✅ (E2E real) | No | query/topic | fuentes (sources) | plan | IMPLEMENTED+VALIDATED |
| outline | `create_book_plan` | ✅ | ✅ (E2E) | No | plan | secciones/outline | plan | IMPLEMENTED+VALIDATED |
| writer | `write_chapter_es` | ✅ | ✅ (E2E det.) | **Sí** | outline+research+sources | draft_es | outline/research | IMPLEMENTED+VALIDATED |
| fact_check | `fact_check_chapter` | ✅ | ✅ (E2E) | **Sí** | draft | claims/issues | writer | IMPLEMENTED+VALIDATED |
| editor | `edit_chapter` | ✅ | ✅ (E2E fallback) | **Sí** | draft | edited_es | writer/fact_check | IMPLEMENTED+VALIDATED |
| image_plan | `create_chapter_image_plan` | ✅ | PARTIAL | **Sí** | draft | plan imágenes | writer | IMPLEMENTED+NOT_FULLY_VALIDATED* |
| image_gen | `generate_chapter_images` | ✅ | PARTIAL | **Sí** | plan | imágenes persistidas | image_plan | IMPLEMENTED+PARTIAL |
| quality_gate | `final_quality_control` | ✅ | ✅ (E2E) | No | libro completo | QC (PASS/WARNING/FAIL) | todo | IMPLEMENTED+VALIDATED |
| docx | `build_book_docx` | ✅ | ✅ (E2E) | No | book_dict | DOCX | todo | IMPLEMENTED+VALIDATED |

\* **image_plan/image_gen**: la orquestación (plan → gen → persistencia → inserción en
DOCX) está implementada en código, pero **la suite/runner E2E principal corre con
`image_count=0`**, por lo que esas dos fases NO se validan end-to-end en el E2E 001.
Su validación es parcial (cobertura unitaria en `tests/test_image_planner.py`,
`modules/image_generator/tests/`). Es una distinción **IMPORTANTE** (IMPLEMENTED ≠ VALIDATED).

**Discrepancia a tener en cuenta:** la lista **no** es la antigua. `AUTOPILOT_PHASES`
actual (10 fases) incluye `image_plan` e `image_gen`, que el **frontend** (`app.js`)
omite y el **runner E2E 001** no ejecuta (porque usa `images=0`).

---

# 5. FLUJO FRONT → AUTOPILOT → DOCX

Flujo real de una petición de creación de libro:

```text
Frontend (formulario nuevo proyecto)
  ↓ POST /api/books   (title, subtitle, idea/description, author, genre,
  ↓                    target_audience, target_chapters, images_per_chapter, layout_config…)
Creación de libro (frontend/editorial.py create_book)
  ↓
Autopilot (core/autopilot.py create_job → run_job → worker)
  ↓
Por cada fase → build_phase_payload(phase, book_id, data)  (frontend/editorial.py)
  ↓
Módulo (via scheduler / módulo real) → resultado
  ↓
Persistencia:
  - fases per-chapter → chapters.{draft_es, edited_es, images, …}
  - plan/research/outline/QC → checkpoints en data/checkpoints/…
  ↓
Document Builder (modules/document_builder/main.py build_book_docx)
  ↓
output/docx/book_{book_id}_{language}.docx   (8E.6: identidad 1:1 por book_id)
```

### Propagación de campos (verificado en código)

| Campo | Origen → Destino |
|---|---|
| `title` / `subtitle` | Frontend → `books` → `book_dict.title/subtitle` → DOCX portada + header |
| `description` (o `idea`) | Frontend → `books.description` (create_book mapea `idea`→`description`, fix 8E.2) → DOCX introducción + `core_properties.comments[:255]` |
| `image_count` / `images_per_chapter` | Frontend → `books.image_count` → `book_dict.image_count` → Document Builder / Image Planner |
| `layout_config` (preset + overrides) | Frontend → `books.layout_config` (JSON) → `_build_book_dict` → Document Builder `_apply_layout_config` |
| `author` / `genre` / `target_audience` | Frontend → `books` → `book_dict` → QC metadata + DOCX legal/portada |
| `languages` / `language` | Frontend → `books.languages` → `book_dict.languages` → DOCX `language` |
| `chapters[].title` | plan → `chapters.title` → DOCX capitular |
| `chapters[].outline` / `edited_es` / `draft_es` | writer/editor → `chapters` → `book_dict.chapters` → DOCX contenido |
| `chapters[].images` | image_gen → `chapters.images` → DOCX inserción de figuras |
| `sources` | research → `SourceManager` (`sources.chapter_ids`) → `_chapter_source_urls()` → `book_dict.chapters[].sources` (8D.2: fuente de verdad = asociaciones reales, nunca globales) |

### Endpoints relevantes (frontend_api.py)

- `POST /api/books` — crear libro (y crear job autopilot)
- `GET /api/books` — listar
- `GET /api/books/<id>` — detalle
- `GET /api/books/<id>/autopilot` — estado del job
- `POST /api/books/<id>/autopilot` — lanzar pipeline
- `GET /api/books/<id>/docx` — servir el DOCX real (8D.2): 200 si completed+existe,
  404 si no existe job, 409 si no completed, 400 si path traversal
- `/api/stream` — Server-Sent Events (job_started, phase_started/completed/failed, etc.)
- `/api/tasks`, `/api/modules`, `/api/metrics`, etc.


---

# 6. BASE DE DATOS

SQLite en `data/space_lair.db` (configurable con `SPACE_LAIR_DB_PATH`). Migraciones
automáticas en `core/database.py::_migrate`.

### `tasks`
Representa una tarea encolada. Campos: `id`, `capability`, `payload` (JSON), `status`
(`pending|running|done|error|pending_approval|cancelled`), `module_id`, `result`,
`error`, `attempts`, `max_attempts`, `created_at`, `started_at`, `finished_at`,
`next_retry_at`, `cost`, `tokens_input`, `tokens_output`.
- **Escribe:** scheduler/task_queue (estados, intentos, métricas).
- **Consume:** scheduler, API, frontend.

### `books` (proyecto editorial)
Campos: `id`, `title`, `subtitle`, `description`, `image_count` (default 3, migrado),
`layout_config` (JSON, migrado), `status`, `languages`, `target_chapters`, `author`,
`genre`, `target_audience`, `created_at`, `updated_at`.
- `image_search_ratio` | REAL, default 0.0 | Proporción de imágenes por capítulo a buscar (SearXNG) vs generar (ComfyUI/local). 0.0 = comportamiento actual sin cambios. Escrito por: create_book (futuro). Consumido por: core/autopilot.py::_run_image_gen_split (IMPLEMENTED+VALIDATED, ratio=0.0 = passthrough exacto). BLOCKED para ratio>0 en producción real: search_chapter_images no está registrada en PAYLOAD_SCHEMAS/OUTPUT_SCHEMAS (core/schemas.py) — próximo paso antes de poder usar ratio>0.
- **Escribe:** `frontend/editorial.py::create_book` (creación) y fases.
- **Consume:** Autopilot, Document Builder, Quality Gate, Frontend.

### `chapters`
Campos: `id`, `book_id`, `number`, `title`, `status`, `research`, `sources` (JSON),
`outline`, `draft_es`, `draft_en`, `edited_es`, `edited_en`, `images` (JSON),
`quality_status`, `created_at`, `updated_at`.

| Campo | Quién lo escribe | Quién lo consume |
|---|---|---|
| `title` | plan (outline) | Document Builder (capitular), QC |
| `outline` | fase outline | writer |
| `draft_es` | writer | editor, fact_check, Document Builder (si no hay edited) |
| `edited_es` | editor | Document Builder (prioridad sobre draft; test 8E6/docx) |
| `images` | image_gen (`persist_chapter_images`) | Document Builder (inserción de figuras) |
| `sources` | (hueco — ver sección 17) | QC `_check_sources` |

> ⚠️ `chapters.sources` **no se puebla** por el chapter_writer actualmente (ver
> Problemas Abiertos #1). El `_build_book_dict` de `frontend/editorial.py` obtiene las
> fuentes reales desde `SourceManager` (`sources.chapter_ids`), no de `chapters.sources`.

### `sources`
Campos: `id`, `url`, `url_hash` (unique), `title`, `publisher`, `author`,
`publication_date`, `accessed_at`, `source_type`, `relevance`, `notes`,
`chapter_ids` (JSON).
- **Escribe:** módulo `research` (SourceManager).
- **Consume:** research→writer/fact_check, QC (`_check_sources`), `_chapter_source_urls()`.

---

# 7. DOCUMENT BUILDER / DOCX

Archivo: `modules/document_builder/main.py` (integra `python-docx`).

### Cómo se construye el DOCX (`build_book_docx`)
Orden real de secciones:
1. **Portada** (`_add_cover`): título, subtítulo, autor.
2. **Legal** (`_add_legal`): copyright / texto legal.
3. **TOC / Índice** (`_add_toc`): lista de capítulos.
4. **Introducción** (`_add_introduction`): desde `book.description`.
5. **Capítulos** (`_add_chapter`): grupo por capítulo, `edited_es` con prioridad sobre
   `draft_es`; se añaden `## Fuentes utilizadas` con las referencias.
6. **Imágenes** por capítulo (con captión "Figura…") si `chapters[].images` no vacía.
7. **Estilos/maquetación** (`_ensure_style`, `_apply_layout_config`): preset + overrides.

### Formato / propiedades
- Filename: **`output/docx/book_{book_id}_{language}.docx`** (fix 8E.6 — identidad 1:1 por libro, sin colisión cross-book).
- `core_properties`: `title`, `author`, `subject`=genre, `comments`=**`(description or "")[:255]`** (fix del error "exceeded 255 char limit"), `language`.
- Header: título del libro. Footer: número de página + nombre de archivo.
- Page margins/size: A4 por defecto (A4/LETTER/LEGAL soportados).

### Markdown soportado (`_parse_markdown_to_paragraphs`)
- **Negrita** (`**texto**`), *cursiva* (`*texto*`).
- Headings `##` … `######` (niveles 2-6; nivel 1 reservado al título del capítulo).
- Saltos de página explícitos (`<!-- page_break -->` / divisor).
- No renderiza listas numeradas de Markdown como listado de Word (se parsean como párrafos); **limitarse al Markdown soportado**.

### Presets de maquetación (FASE 6)
Cinco presets en `PRESETS` (ver sección 9).

### Problemas corregidos en DOCX

| Problema | Estado |
|---|---|
| Título de capítulo duplicado (heading repetido) | FIXED + VALIDATED (tests de document_builder) |
| Página en blanco al inicio | FIXED + VALIDATED |
| Alineación del texto legal | FIXED + VALIDATED |
| Markdown literal (no renderizado) | FIXED + VALIDATED |
| Títulos incorrectos en TOC | FIXED + VALIDATED |
| Introducción vacía | FIXED + VALIDATED (deriva de description) |
| **`comments` > 255 chars → ValueError en librería** | FIXED + VALIDATED (`[:255]`) |
| Nombre de archivo colisionaba entre libros | FIXED + VALIDATED (`book_{book_id}_{lang}`) |


---

# 8. IMÁGENES

### Estado REAL por capa

```text
ORCHESTRATION (image_plan → image_gen):  IMPLEMENTED
PERSISTENCE (archivos + metadata + chapters.images): IMPLEMENTED (+ persist_chapter_images)
DOCX INSERTION (figuras con caption): IMPLEMENTED
REAL AI IMAGE PROVIDER: SÍ — ComfyUI (SDXL Base+Refiner) es DEFAULT_PROVIDER desde 2026-08-17; fallback automático a local si ComfyUI no responde (timeout de conexión 10s + guard de presupuesto interno 330s)
```

### Detalle
- **Configuración de `image_count`:** el Frontend (`index.html` "Imágenes por capítulo",
  radios 0/1/3/5, default 3) lo envía como `images_per_chapter` → `books.image_count`.
  El payload E2E usa `image_count=0` (sin imágenes).
- **Planificación:** `modules/image_planner/main.py` — genera `ImageSpec`s; default 3
  imágenes por capítulo con roles distintos (hero/diagram/scene), prompts compatibles
  con generadores locales.
- **Generación:** `modules/image_generator/main.py` (`generate_image`,
  `generate_chapter_images`). Persiste PNG + `*.metadata.json` en
  `data/images/books/{book_id}/chapters/{chapter_number}/images/`.
- **Proveedor activo:** `core/image_providers/registry.py` con
  `DEFAULT_PROVIDER = "comfyui"` → `ComfyUiProvider`
  (`core/image_providers/comfyui.py`) que genera **imagen IA real** (SDXL Base+Refiner).
  Si ComfyUI no responde (timeout de conexión 10s, `COMFYUI_CONNECT_TIMEOUT`) o se agota el
  presupuesto interno de la fase (330s), cae automáticamente a `LocalImageProvider`
  (`local.py`, placeholder sin dependencias, SOLO stdlib) como fallback limpio.
- **Persistencia en capítulo:** `frontend/editorial.py::persist_chapter_images` escribe
  las rutas en `chapters.images`.
- **Consumo en DOCX:** `document_builder` inserta cada ruta de imagen en su capítulo con
  caption.
- **Proveedor ComfyUI (2026-08-17, ACTIVADO POR DEFECTO):** `core/image_providers/comfyui.py`
  reescrito — workflow **SDXL Base+Refiner real** (dos pasadas, `DEFAULT_WORKFLOW`), sustituciones
  por imagen, `COMFYUI_CONNECT_TIMEOUT` corto (10s) para el POST de encolado, poll con
  `IMAGE_TIMEOUT=120` (`COMFYUI_POLL_MAX_WAIT=300` para el bucle), guard de presupuesto interno
  (330s) y fallback limpio. Validado con servidor ComfyUI real (0.33.1) y script
  `tools/validate_comfyui.py`: imagen real (189k colores), 1:1 y 16:9 OK, ~72–95 s/imagen;
  fallback sin excepción. **`DEFAULT_PROVIDER = "comfyui"`** y en `_register_defaults`
  `ComfyUiProvider` se registra con `default=True` (local queda como fallback si no está
  disponible). Resolución por default (`get(None)`) confirmada contra servidor real. Ver §16.

> **NOTA:** La **orquestación**, la **persistencia** y la **inserción DOCX** están implementadas.
> Desde **2026-08-17 el proveedor de imágenes real activo es ComfyUI (SDXL Base+Refiner)** — **sí**
> hay imagen IA real por defecto (**DEFAULT_PROVIDER="comfyui"**). Si ComfyUI no está disponible o
> se agota el presupuesto interno de la fase, cae a placeholder local automáticamente (fallback
> limpio, no es error).

---

# 9. PRESETS DE MAQUETACIÓN

Sistema de `layout_config` en `modules/document_builder/main.py`.

### Presets reales (clave canónica)
| Preset | Font | Heading color | Alineación cuerpo | Font size | Line spacing | Imágenes/capítulo |
|---|---|---|---|---|---|---|
| `editorial` | Georgia | `#1F3A5F` | justify | 11 | 1.15 | 3 |
| `moderno` | Arial | `#6A3FB5` | left | 11 | 1.2 | 3 |
| `clasico` | Times New Roman | `#000000` | justify | 12 | 1.5 | 1 |
| `academico` | Garamond | `#1F3A5F` | justify | 11 | 1.5 | 1 |
| `dossier` | Arial | `#000000` | left | 10 | 1.15 | 0 |

### Aliases tolerantes (con/sin acentos)
`editorial`↔`editorial` · `moderno`↕`modern` · `clasico`↕`classic` · `academico`↕`academic` · `dossier`↔`dossier`.

### Overrides
Los overrides del usuario se aplican ENCIMA del preset (`_effective_layout`):
`font_family`, `heading_font`, `heading_color`, `body_alignment`, `font_size`,
`line_spacing`, `images_per_chapter`.

### Viaje Front → Document Builder
Frontend (`index.html` selects: preset, fuente, color títulos, alineación) →
`POST /api/books` → `books.layout_config` (JSON) → `editorial._build_book_dict`
(`_parse_layout_config`) → `Document Builder._apply_layout_config`.


---

# 10. WRITER (Chapter Writer)

Archivo: `modules/chapter_writer/main.py` — **PROTECTED** (solo se modifica con
aprobación; proyecto fase 7.9D.7).

### El problema de timeout anterior (causa raíz)
El scheduler aplicaba `timeout_seconds=180` externamente. Un Ollama lento/bloqueado
podía agotar ese timeout **sin** que el writer activara su fallback/backstop, lo que
dejaba tareas muertas y capítulos cortos/duplicados.

### Fix aplicado (código actual)
- **Timeout interno de proveedor:** `WRITER_PROVIDER_TIMEOUT=60`, `WRITER_MAX_RETRIES=1`.
  Se muta SOLO la instancia local devuelta por `registry.get()` (nueva por llamada → no
  altera config global) para que un LLM lento lance timeout interno y active el
  fallback/backstop antes del timeout del scheduler.
- **Presupuesto total de tiempo:** `WRITER_TOTAL_TIME_BUDGET=150.0` s (con holgura bajo
  180s). `_llm_budget_exhausted()` aborta el bucle de continuación y delega en el
  **backstop determinista**.
- **Presupuesto de continuaciones determinista:** derivado del déficit real
  (`_plan_continuation_deficit`) con `AVG_WORDS_PER_CONTINUATION=700`.
- **Límite duro absoluto:** `ABSOLUTE_HARD_LIMIT=8` llamadas de continuación → **nunca
  hay bucle infinito** (segmento real + segmento target).
- **Duplicate continuation guard:** las propuestas duplicadas/`rejected_duplicate`/
  `rejected_heading` **no finalizan**; se descartan y se reintenta si queda presupuesto.
  Estados `repeated`/`rejected_full_chapter`/`stop_insignificant`/`error` sí detienen la
  fase. `_MIN_CONTINUATION_WORDS=5`.
- **Backstop determinista sin LLM:** `_deterministic_complete()` amplía las secciones
  existentes con párrafos 100% Python (`_deterministic_section_paragraphs` +
  `_elaborate_fact_deterministic`, variación por seed y rotación de hechos) hasta
  alcanzar `minimum_words`, **sin headings nuevos ni placeholders ni duplicación
  literal**.
- **Flags de entorno:** `CHAP_USE_LLM` (1=LLM activado; 0=modo sin LLM) y
  `CHAP_FORCE_MIN` (1=forzar mínimo ≥1500). Aplicados en
  `run_e2e_001_editorial.py::_configure_environment()` **desde `main()`** (no a nivel
  de import) para no contaminar la colección de pytest (fix 7.9D.7 flaky).

### Por qué funciona
El **control es 100% Python**: el LLM solo genera texto; Python decide objetivo,
tamaño de petición, cuándo continuar, qué rechazar (duplicados) y cuándo terminar y
completar deterministamente. Nunca el LLM es la autoridad de finalización.

### Cómo se validó
- `e2e_001_report.json`: `chapter_execution_mode=deterministic`, `chapter_word_count=1668`
  (≥1500), `chapter_placeholder_detected=false`, `chapter_generation_status=PASS`.
  `chapter_quality_errors` contiene **solo avisos** de "duplicación potencial" y
  "continuación rechazada por repetición" → **no bajan el quality gate**.
- Tests: `tests/test_chapter_writer.py` (1781 líneas) y `tests/test_runner_e2e_001.py`.

### Comportamiento esperado
- **Ollama OK:** LLM genera + continuaciones hasta target, con guarda de duplicados.
- **Ollama falla/lento:** timeout interno → fallback `_fallback_chapter` (100% Python)
  + backstop determinista `_deterministic_complete` → cumple mínimo sin depender del LLM.

---

# 11. EDITOR

Archivo: `modules/editor/main.py` (quitado de edición automática; OUT_OF_SCOPE salvo
nuevo problema con evidencia).

### Fix aplicado
- `EDITOR_PROVIDER_TIMEOUT=60`, `EDITOR_MAX_RETRIES=1` (mismo patrón que writer: instancia
  local, no global).
- `MAX_EDITOR_TOKENS=16000`, `MIN_EDITOR_TOKENS=1024`, factor `1.25` →
  `min(MAX, max(MIN, int(input_words*1.25)))`.
- **Fallback:** si el LLM falla o devuelve texto inválido/vacío → devuelve el capítulo
  **sin cambios** (`_fallback_edit`) con notas. Si la salida es demasiado corta
  (`< minimum_words` o `ratio_floor`) y hay retry disponible, reintenta; si no, conserva
  el original y marca `execution_mode=fallback`.

### Problema original
Igual que writer: un LLM lento podía agotar el timeout del scheduler (180s) sin
activar el fallback → tareas muertas / ediciones perdidas.

### Resultado E2E conocido
`e2e_001_report.json`: `editor_execution_mode=fallback` (el LLM no devolvió edición
válida → se conservó el texto), `editor_status=PASS`, `editor_placeholder_detected=false`.
Tests: `tests/test_editor.py`.


---

# 12. RETRIES Y TIMEOUTS (CONCEPTOS CLAVE)

**Para evitar malentendidos de una IA futura**, distinguir exactamente:

| Concepto | Significado real en el código | Dónde |
|---|---|---|
| **task-level retry** | Reintentos de la **tarea** en la cola `tasks` (campo `attempts`/`max_attempts`). El scheduler `_process_task` reintenta la tarea a nivel de cola si el módulo falla. | `core/scheduler.py`, `task_queue` |
| **phase-level retry** | Reintentos de una **fase** del Autopilot (`PHASE_RETRY`). `DEFAULT_MAX_ATTEMPTS=2`, `DEFAULT_BACKOFF_STEP=2.0`. Las fases per-chapter no repiten capítulos ya `PASS`. | `core/autopilot.py` |
| **scheduler timeout** | Timeout externo del módulo en `module.json` → `timeout_seconds` (p.ej. chapter_writer=180, editor=180, planner=120, research=120). Ejecuta vía `ThreadPoolExecutor.future.result(timeout)`. | `core/scheduler.py` `_get_timeout` |
| **provider timeout** | Timeout de la llamada LLM dentro del módulo (writer=60, editor=60). Menor que el del scheduler para activar fallback a tiempo. | `modules/chapter_writer`, `modules/editor` |
| **provider retries** | `WRITER_MAX_RETRIES=1`, `EDITOR_MAX_RETRIES=1` (reintentos internos de la llamada LLM). | `modules/chapter_writer`, `modules/editor` |
| **fallback** | Resultado determinista 100% Python cuando el LLM falla (writer: `_fallback_chapter`+backstop; editor: `_fallback_edit`). | `modules/chapter_writer`, `modules/editor` |

### Qué ocurre cuando…
- **una task falla:** `task_queue.fail_task` (o retry a nivel de cola si `attempts < max_attempts`, con `next_retry_at`).
- **una fase falla:** el Autopilot marca `PHASE_FAIL`, decide si reintenta (phase-level
  retry, solo capítulos pendientes en per-chapter) y, si agota intentos, el job pasa a
  `FAILED`.
- **una fase está `PASS`:** no se vuelve a ejecutar en reintentos posteriores.

### Lo que muestra el Front
- **"Intentos job":** `_serialize_job` deriva `attempts` = **máximo de intentos por fase**
  (agregado de datos reales, no inyectado).
- **Duración mostrada:** suma de duraciones reales de fases (agregado derivado, no inventado).
- El front no inventa porcentajes ni estados; representa el estado real del backend.

---

# 13. FRONTEND / UX ACTUAL

Archivos: `frontend/index.html`, `frontend/app.js`, `frontend/style.css`, `frontend_api.py`.

### Pantallas / vistas (navegación)
- **SALA DE CONTROL** (`control-room`): pipeline visual del flujo editorial con
  módulos/estaciones.
- **LIBROS** (`books`): selector de libros, detalle, crear proyecto.
- **ACTIVIDAD** (`activity`): tareas, logs, feed de eventos.
- **SISTEMA** (`config`): módulos/estado.

### Creación de libro (`#book-create-modal`)
Campos: **Título***, **Capítulos objetivo** (`new-book-chapters`, default 1, min 1),
**Idea/descripción** (`new-book-idea`), **Autor/a**, **Género**, **Público objetivo**,
**Imágenes por capítulo** (radios 0/1/3/5, default 3), y **MAQUETACIÓN**: Preset
(editorial/moderno/clásico/académico/dossier), Fuente, Color títulos, Alineación.

### Pipeline visual (`AUTOPILOT_PHASES` en `app.js`)
`planner, research, outline, writer, fact_check, editor, image_plan, image_gen,
quality_gate, docx` → **10 fases** (espejo exacto del backend desde FASE 8F.3).

> **Nota histórica:** entre la 8E.x y 8F.2 el front mostraba solo 8 fases (pipeline -->
> `image_plan`/`image_gen` omitidas). La discrepancia se cerró en FASE 8F.3 (ver §16).

### Estados que muestra
- Job: `PENDING/RUNNING/FAILED/COMPLETED/CANCELLED` (mostrados como ESPERANDO/EN
  EJECUCIÓN/CANCELADO/COMPLETADO/FALLIDO).
- Fase: `PENDING/RUNNING/RETRY/PASS/FAIL` (+ reintento).
- Feed SSE: `phase_started/completed/failed`, `job_completed/failed`, `task_*`,
  `central_ai_decision`.

### Limitaciones conocidas del front
- Textos de interfaz en español. Se mantienen intencionalmente en inglés los siguientes términos (nombres de marca, formatos de archivo, abreviaturas técnicas y niveles de log): `SSE`, `DOCX`, `Autopilot`, `job`, `QC`, `Preset`, `LOG`/`INFO`/`WARN`/`ERR`, iconos simbólicos (`[OK]`), y nombres de fuentes (`Georgia`, `Arial`, `Times New Roman`, `Garamond`, `Calibri`).
- No expone la configuración de imágenes IA (proveedor) en la UI.


---

# 14. TESTS Y VALIDACIONES

> ⚠️ **Advertencia:** no se ejecutó la suite completa durante la elaboración de este
> documento (tarea de inventario, regla 10 del prompt). Los conteos son los
> **registrados** en estado/documentos, no re-ejecutados aquí.

| Área | Tests | Resultado conocido | Última validación | Tipo |
|---|---:|---:|---|---|
| book_planner | tests/test_book_planner.py | PASS | see estado | unit |
| chapter_writer | tests/test_chapter_writer.py (1781 L) + test_chapter_writer_placeholder.py | PASS | see estado | unit |
| editor | tests/test_editor.py | PASS | see estado | unit |
| fact_checker | tests/test_fact_checker.py | PASS | see estado | unit |
| translator | tests/test_translator.py | PASS | see estado | unit |
| image_planner | tests/test_image_planner.py | PASS | see estado | unit |
| source_manager | tests/test_source_manager.py + test_editorial_sources.py | PASS | see estado | unit |
| autopilot | tests/test_autopilot*.py (editorial, document_output, quality_gate_payload, research_sources) | PASS | see estado | unit |
| editorial panel | tests/test_editorial_panel.py, test_editorial_metadata.py | PASS | see estado | unit |
| document_builder | modules/document_builder/tests/test_document_builder.py + test_e2e_docx_integration.py | PASS | see estado | unit/integration |
| frontend API | tests/test_frontend_api*.py (+ test_frontend_api_docx.py, 8D.2) | PASS | see estado | integration |
| quality gates | tests/test_quality_gates.py | PASS | see estado | unit |
| E2E runner | tests/test_runner_e2e_001.py | PASS | see estado | E2E (mocked) |
| E2E CLI real | `run_e2e_001_editorial.py` | **completed, PASS** | 2026-08-14 23:23:41 | E2E real |

### LAST KNOWN FULL SUITE
```text
786 passed, 0 failed, 0 errors, 1 skipped   (tests/ + modules/, incl. E2E runner) — 2026-08-28 (315.30s)
checkpoint post-commits §17 #35(F1-F3)/#36(F1-F5)/#37, sin regresión
```
Histórico: 601 passed (2026-08-16, checkpoint 8K.3). Incrementos desde 8K.1: +1 test (`test_real_query_los_dooms_stopwords_filtro` en `tests/test_research_sources.py`, FASE 8K.3).
También referenciado: "503 tests verdes" excluyendo el E2E pesado (`test_runner_e2e_001.py`).
Prueba focalizada research (8K.2/8K.3): 36/36 PASSED (`test_research_sources.py`, `test_research_multisource.py`, `test_research_curation.py`, `test_quality_gates.py`).

# 15. EVIDENCIA E2E

### E2E CLI real (`run_e2e_001_editorial.py` → `e2e_001_report.json`) — canónico

| Campo | Valor |
|---|---|
| fecha aprox. | 2026-08-14 23:23:41 |
| book | book_id=1001 ("El nacimiento de Internet") |
| nº capítulos | 1 |
| modo | `chapter_execution_mode=deterministic` (backstop); editor `fallback` |
| resultado | `status=completed`, **8/8 etapas PASS** (planner/research/outline/chapter/fact_check/editor/QC/document_builder) |
| investigación | research real (Wikipedia), 9 fuentes reales |
| capítulo | `chapter_word_count=1668`, `placeholder_detected=false`, quality errors = avisos de duplicado potencial (no FAIL) |
| DOCX | `docx_status=PASS`, `output/docx/book_1001_es.docx` (book_id=1001, lang=es, 1 capítulo, 0 imágenes) |
| QC | `qc_overall_status=PASS` (metadatos, fuentes, imágenes, documento: PASS) |
| warnings | `fallback_warning=true` (fallback_chapter), `fallback_reasons=["fallback_chapter"]` |
| checkpoints | último en `data/checkpoints/1001/book/final_qc/v0037.json` |

### E2E real book_43 — integración de los 4 fixes de la sesión 2026-08-22/23

| Campo | Valor |
|---|---|
| fecha | 2026-08-23 (servidor reiniciado PID 24796, 16:08:18, posterior a todos los módulos tocados) |
| book | book_id=43, "Expediciones polares a la Antártida" (Historia; sin relación con Doom/videojuegos) |
| params | target_chapters=2, image_count=3, image_search_ratio=0.5, author/genre/target_audience explícitos |
| resultado | **10/10 fases PASS, job COMPLETED** |
| research | PASS (modo=llm), 8 fuentes limpias (5 Wikipedia + 3 web, todas temáticamente coherentes); sin contaminación cruzada de fuentes Doom/Marvel previas |
| fact_check | PASS ambos capítulos, claims_checked=0; guard anti-inflado activo (WARNING informativo: claims_checked forzado a 0 cuando issues vacío) |
| image_gen | PASS (375s); split ratio 0.5 funcionando: 3 imágenes web (search) + generación ComfyUI SDXL; 0 warnings de denylist (ninguna imagen candidate era denylisted) |
| quality_gate | overall=WARNING (no bloqueante); check de imágenes "3 imágenes por capítulo" PASS contra image_count=3 REAL del libro (sin literal hardcodeado) |
| docx | `output/docx/book_43_es.docx`, 2.946.433 bytes |
| confirmación | Valida en producción real y simultánea los 4 fixes de la sesión 2026-08-22/23 (anti-reciclaje de fuentes, dedupe de claims en fact_checker, denylist de dominios en image_search, fix de image_count hardcodeado en quality_gate). Hallazgos menores derivados → §17 #11 y #12. |

> **Distinción clave:** `e2e_output.txt` muestra una **ejecución ANTERIOR/stale** donde el
> **document_builder FALLÓ** (`ValueError: exceeded 255 char limit for property` en
> `core_properties.comments`). Ese error **ya está corregido** (`[:255]`), y el reporte
> canónico actual es PASS. **No mezclar** como "E2E fallido".
### E2E real book_46 / book_47 — hallazgos writer/fact_checker (2026-08-23)

> Solo-lectura: writer fabrica citas APA falsas (book_47 ch192) y omite/trunca colas de fuentes
> (book_46 ch189/ch190, book_47 ch191/ch193) pese a recibir fuentes reales en payload;
> fact_checker no las detecta (#15). DOCX book_46/book_47 verificados existentes en
> output/docx/ con timestamps coincidentes con tasks 132/158 (ver §17 #16).
> Detalle completo: §17 #13–#16. CAUSA RAÍZ COMÚN confirmada para #9/#13/#14: ver nota en §17 fila #9
> (document_builder no tiene fuente propia; la cola la genera el LLM del writer por instrucción del prompt).

---

# 16. PROBLEMAS RESUELTOS

### E2E Front
Validado por tests de integración (`tests/test_frontend_api_autopilot.py`,
`test_frontend_api_docx.py`) que fijan el comportamiento del endpoint DOCX (200/404/409/400).

---

# 16. PROBLEMAS RESUELTOS

| Problema | Causa | Fix | Archivos | Validación | Estado |
|---|---|---|---|---|---|
| §17 #16 (deuda de hardening): build_book_docx llamaba a doc.save(path) sin try/except ni verificación posterior — un guardado fallido a medias (disco lleno, permiso denegado, proceso interrumpido) podía reportar éxito con un DOCX corrupto o inexistente (el escenario original "DOCX fantasma" de #16; premisa ya corregida 2026-08-23, quedaba solo esta deuda menor) | doc.save() sin ningún control de error ni verificación post-save del fichero resultante | try/except alrededor de doc.save() que captura cualquier excepción de I/O y la propaga como RuntimeError con mensaje claro ("No se pudo guardar el DOCX en '<path>'"), encadenada con `from exc`; tras el save, verificación os.path.exists(path) + os.path.getsize(path) > 0, lanzando RuntimeError si el fichero no existe o es de tamaño 0. Se reutiliza el contrato de excepción ya usado por validate_payload (el scheduler traduce excepciones a fallo de fase) — core/autopilot.py SIN CAMBIOS. Ni _add_cover/_add_legal/_add_toc, ni nombre/ruta del fichero, ni otra lógica tocada | modules/document_builder/main.py (OUT_OF_SCOPE, autorización puntual; líneas ~840-854), modules/document_builder/tests/test_document_builder.py | 2 tests nuevos: test_docx_save_failure_propagates_as_error (mock de Document.save con OSError(28) → RuntimeError propagado, nunca PASS silencioso) y test_docx_save_success_path_unchanged (regresión camino feliz: fichero existe y getsize>0). pytest modules/document_builder/tests/test_document_builder.py → 32/32 PASS (3.02s); suite completa NO ejecutada | FIXED + VALIDATED (2026-08-26) |
| §17 #28: capabilities únicas de imágenes (search_chapter_images/generate_chapter_images) con una sola lógica de query+anclaje compartida con research (_has_anchor_keyword, monolingüe ES y pensado para texto rico) — en libros bilingües/EN la búsqueda seguía construyéndose desde título/texto ES y el anclaje comparaba keywords ES contra candidatos cuya señal textual real son slugs/títulos EN (haystack ≈ URLs de imagen y página), descartando en masa resultados web claramente on-topic. Evidencia real book_67 («Todo sobre el café, descubrimientos, tipos, cafe en el mundo»): unsplash «a-cup-of-coffee-sitting-on-top-of-a-white-counter» y pexels «anonymous-barista-pouring-water-into-filter» logueados como «resultado descartado por no anclarse al tema». Distinto de §17 #24 (ese fix añadía param language=en de SearXNG y title_en al generador IA; no tocaba query nativa ni filtro de anclaje) | Capability única sin distinción de idioma nativo ni para la query de búsqueda ni para el haystack/anclaje temático; además, sin normalización de acentos («café» ≠ «cafe») ni traducción ES↔EN | FIX EN 2 PASOS. (1) modules/image_search/main.py (+module.json): nuevas capabilities search_chapter_images_es/_en con routing en execute(); helper local _has_anchor_keyword_img(topic, cand, language) — stopwords ES REUTILIZADAS de research vía import (_STOPWORDS_ES, fallback local si research ausente) y lista mínima EN propia (_ANCHOR_STOPWORDS_EN), MISMO umbral que research (hits>=1 tema de 1 palabra / hits>=2 multi-palabra, NO relajado: la causa era el idioma, no el umbral); query EN nativa _search_query_en (title_en > chapter_title_en > primeras palabras de chapter_text_en > cadena genérica EN, nunca cae a ES). Denylist de dominio y descarga: sin cambios, compartidos. (2) modules/image_generator/main.py (+module.json): generate_chapter_images_es/_en vía generate_chapter_images_lang() que fija language y delega (el plan ya llega separado por idioma desde image_planner; ComfyUI/local sin cambio). (3) core/schemas.py: las 4 capabilities registradas en PAYLOAD_SCHEMAS/OUTPUT_SCHEMAS REUTILIZANDO clases existentes (ImageSearchPayload/ImageGeneratePayload → ImageGenerateOutput, plug-compatible como el P1 original de §19) + campo opcional topic_en para el anclaje EN. (4) core/autopilot.py: nuevo _resolve_image_capabilities(book) (mismo patrón que _resolve_writer_capability, books.languages) usado en _run_single y _run_image_gen_split; el payload EN incluye topic_en (job.data.topic_en > books.title_en) y title_en; AUTOPILOT_PHASES y orden de fases SIN CAMBIOS. (5) frontend/editorial.py: build_payload añade topic_en/title_en a image_plan e image_gen (contrato único entre ambas). SIN tocar: frontend/index.html/app.js (UI = paso 3 posterior), modules/research/main.py, modules/image_planner/main.py, modules/chapter_writer/main.py | modules/image_search/main.py, modules/image_generator/main.py (+ambos module.json — OUT_OF_SCOPE autorizados), core/schemas.py, core/autopilot.py, frontend/editorial.py; tests/modules/image_search/tests/test_image_search.py, tests/modules/image_generator/tests/test_image_generator.py, tests/test_schemas_image_search.py, tests/test_autopilot_persist_chapters.py, tests/test_editorial_bilingual_plan.py | Paso 1: 7 tests nuevos en test_image_search.py (book_67 unitario: el candidato de café EN ANCLA con topic EN nativo y NO ancla con topic ES — cada idioma busca lo suyo; regresión §17 #11 tipo comicvine descartada en AMBAS variantes con su on-topic OK; routing execute() ES/EN con query nativa «The Echo Cave» y language=en solo en *_en; registro en module.json; pipeline E2E mock unsplash/pexels: EN descarga, ES descarta sin descargar nada) + 2 en test_image_generator.py (capabilities registradas; shape ImageGenerateOutput válido y etiquetado de idioma ES/EN; legacy generate_chapter_images intacto). Durante el paso 1 se corrigió un candidato MAL CONSTRUIDO en el propio test de regresión comicvine (título español evaluado bajo variante EN): fix de TEST, sin segundo cambio de producción. Paso 2: pytest test_schemas_image_search.py + test_autopilot_persist_chapters.py + test_editorial_bilingual_plan.py → 25 passed, 0 failed (0.73s). Suite completa NO ejecutada | FIXED + VALIDATED (2026-08-26) |


| §17 #27: research trae snippets de redes sociales (TikTok/Instagram) con metadata de engagement (fecha+likes+comentarios) como fuente citable y el writer LLM los copia verbatim en la prosa del capítulo — el fact_checker bloquea correctamente el resultado (patrón fecha+nombre+cifra → ERROR estructural), pero la causa raíz estaba en research, no en fact_checker. Evidencia real book_66 / chapter_id=458 / task_id=987: payload.sources[8] (tiktok.com/@teban_cometacos) y [9] (instagram.com/reel/...) devueltos por SearXNG con source_type=web_searxng; su content ("1181 me gusta,30 comentarios...", fechas "25 ene 2026"/"3 jul 2026") aparecía copiado verbatim en payload.chapter_text de la misma task; cadena causal research→writer→fact_checker confirmada dato a dato en diagnóstico de solo lectura 2026-08-26 | _multi_source_search recolectaba candidatos de todos los backends sin ningún filtro de dominio; SearXNG devuelve posts sociales como resultados web ordinarios y entraban a curación/ranking (LLM o determinista) como fuentes válidas | Nuevo `_SOCIAL_MEDIA_DENYLIST` = {tiktok.com, instagram.com, facebook.com, twitter.com, x.com} + helper `_is_social_media()` (netloc sin `www.`, comparación por contenido para cubrir subdominios, tolerante a None/"", mismo criterio que `_is_denylisted` de image_search) en modules/research/main.py; filtro aplicado en el bucle de recolección de `_multi_source_search` ANTES del dedupe y de cualquier curación/ranking: descarte silencioso (no ocupa slot, no lanza error). NO se tocaron `_has_anchor_keyword`, `_keyword_overlap` ni resto de lógica existente | modules/research/main.py (OUT_OF_SCOPE, autorización concedida), tests/test_research_sources.py | 2 tests nuevos: test_social_media_candidates_discarded_before_curation (reproduce book_66: tiktok/instagram/subdominio es.tiktok.com descartados; 3djuegos/as.com/infobae/es.wikipedia pasan intactos), test_is_social_media_helper (unitario). pytest test_research_sources.py + test_research_multisource.py + test_research_curation.py → 30/30 PASS (3.93s); suite completa NO ejecutada | FIXED + VALIDATED (2026-08-26) |

| §17 #26: un placeholder de LocalImageProvider (fallback cuando ComfyUI no responde) quedaba persistido en chapters.images sin ningún metadata.json que lo respaldara — evidencia real: libro 65 cap.1, ruta data/images/local/934705850_a938610a44c0.png (color sólido) presente en chapters.images pese a no existir metadata asociada en el directorio del capítulo. | Causa raíz más precisa que la hipótesis inicial: no es que el placeholder se cuele sin metadata en general, sino que persist_chapter_images acumula/mergea rutas entre re-ejecuciones del mismo capítulo sin purgar las de una pasada anterior. Caso real: una primera pasada (ComfyUI caído) generó img_01_hero con provider=local (ruta global fuera del directorio del libro, core/image_providers/local.py nunca escribe metadata.json por diseño); una segunda pasada (ComfyUI sano) regeneró el MISMO image_id y sobrescribió su metadata.json con datos comfyui, pero la ruta local de la primera pasada quedó persistida en chapters.images sin metadata que la respalde ya. _run_image_gen_split y _dedupe_by_path persisten/deduplican por image_path sin verificar coherencia provider↔metadata. | En frontend/editorial.py (NO protegido): nueva _chapter_images_metadata_dir() resuelve el directorio real del capítulo (mismo layout que image_generator._images_dir, respeta IMAGE_STORAGE_ROOT); nueva _filter_orphaned_local_images() descarta rutas 'local' (placeholder) que NO tengan un *.metadata.json correspondiente en ese directorio antes de persistir en chapters.images (fast-path sin I/O si no hay rutas locales); rutas comfyui/web sin cambio de comportamiento. persist_chapter_images invoca el filtro antes del UPDATE y devuelve 'discarded_orphaned_local' para trazabilidad. core/autopilot.py (_run_image_gen_split, _dedupe_by_path) y core/image_providers/local.py NO fueron tocados. | frontend/editorial.py, tests/test_editorial_panel.py | 26/26 PASS en tests/test_editorial_panel.py (24 preexistentes + 2 nuevos: test_persist_chapter_images_discards_orphaned_local_without_metadata cubre ruta local huérfana descartada, ruta local CON metadata conservada, ruta comfyui sin cambio; test_persist_chapter_images_keeps_all_when_no_local_paths regresión sin rutas locales). | FIXED + VALIDATED (2026-08-25) |

| §17 #22: book_planner._execute (llamada LLM principal del plan) usaba max_tokens=2000 FIJO, sin escalar con target_chapters — con ~20 capítulos el JSON de salida se truncaba a mitad del array 'chapters', json.loads fallaba ('Expecting value'), y el sistema caía silenciosamente al plan fallback determinista sin dejar rastro del texto crudo recibido. Evidencia: book_62 en producción real. | max_tokens hardcodeado sin relación al volumen de capítulos pedido; manejo de la excepción de parseo no conservaba el texto crudo del LLM. | Nueva función _planner_max_tokens(target_chapters): min(MAX_PLANNER_TOKENS=6000, max(MIN_PLANNER_TOKENS=2000, PLANNER_BASE_TOKENS=400 + PLANNER_TOKENS_PER_CHAPTER=150 * target_chapters)). logger.debug con los primeros 2000 chars del texto crudo del LLM antes de caer al fallback (nivel DEBUG, no ensucia producción). La llamada de traducción EN del fix §17 #21 (max_tokens=1500) NO fue tocada. | modules/book_planner/main.py (OUT_OF_SCOPE, autorizado), tests/test_book_planner.py | 62/62 PASS en tests/test_book_planner.py (60 preexistentes + 2 nuevos: test_max_tokens_scales_with_target_chapters verifica tc=1→piso 2000, tc=5→2000, tc=20→3400, tc=60→capado 6000; test_planner_logs_raw_on_json_parse_failure confirma logging del raw sin afectar el fallback). | FIXED + VALIDATED (2026-08-25) |

| fact_checker LLM asigna ERROR bloqueante por juicio subjetivo de exactitud en claims sin firma de fabricación estructural ni marcador de falta de soporte (§17 #25, evidencia book_65/book_64) — veredicto inestable entre reintentos del mismo claim | El LLM del fact_checker asigna ERROR por juicio de exactitud propio, sin pasar por _has_fabrication_signature() ni _is_unsupported_issue() (capas del fix §17 #20); ese ERROR "puro" no tenía ninguna verificación adicional antes de bloquear el gate | _verify_error_consistency(): segunda pasada LLM binaria y estricta (ERROR/DEFENDIBLE, max_tokens=8, temp=0.0, FACT_CHECK_CONSISTENCY_TIMEOUT=20s) SOLO para claims ERROR subjetivas (no estructurales) y solo en execution_mode='real'. Si no confirma o falla/timeout -> degrada a WARNING (fail-safe hacia menos bloqueo). Las fabricaciones estructurales de §17 #20 NUNCA pasan por esta segunda pasada, siguen bloqueando sin cambios. core/autopilot.py SIN CAMBIOS. AMPLIACIÓN 2026-08-25: diagnóstico posterior con datos reales (tasks 890/891, mismo capítulo 431 "café Liberica") mostró que la segunda pasada de consistencia se autoconfirmaba de forma estable (temperature=0.0) cuando la claim no tenía NINGUNA fuente (source_url=null) — mismo criterio subjetivo sin ancla, sin información nueva entre pasadas. Ajuste: si el ERROR no estructural tiene source_url=None/vacío, se degrada DIRECTAMENTE a WARNING sin invocar la segunda pasada (ahorra la llamada LLM extra, evita el bucle de autoconfirmación; trazable vía consistency_check="SKIPPED_NO_SOURCE"). Con source_url presente, sigue usando la segunda pasada como antes. Fabricaciones estructurales (§17 #20) sin cambios | modules/fact_checker/main.py (OUT_OF_SCOPE, autorizado), tests/test_fact_checker.py | 24/24 PASS en tests/test_fact_checker.py (2 tests nuevos: skip sin fuente con 0 llamadas al provider, regresión con fuente sigue en 2 llamadas). Simulación con datos reales de book_65 confirmó degradación correcta a WARNING. Presupuesto documentado: peor caso 5 claims ERROR no estructurales × 20s = 100s < timeout_seconds 180 del módulo. AMPLIACIÓN 2 (2026-08-25): timeout duro del scheduler (180s) detectado en producción sobre el mismo libro (task_id=895) — el presupuesto asumido en la ampliación anterior (N=5 claims × 20s = 100s) no cubría capítulos con más claims ERROR con source_url presente (diagnóstico previo de la task reportaba 10; Verificación posterior con datos reales de BD (task 895/894, mismo capítulo 'El Proceso de Preparación del Café', book 65): conteo real 14 claims totales, 2 ERROR con source_url (no 10 como se estimó inicialmente); el timeout de 180s se explica por el tiempo total del pipeline, no solo por la pasada de consistencia. El riesgo estructural (peor caso N×20s sin techo agregado) seguía siendo real y el fix lo elimina matemáticamente: T_total ≤ BUDGET+TIMEOUT = 140s < 180s, independiente de N). Con N≥6, N×20s podía superar los 180s del scheduler. Fix: nueva FACT_CHECK_CONSISTENCY_TOTAL_BUDGET=120s (env-overridable, mismo patrón que WRITER_TOTAL_TIME_BUDGET) compartida entre todas las claims de una misma ejecución; si no queda presupuesto suficiente para completar el peor caso de una llamada más (budget-elapsed < TIMEOUT), la claim restante se degrada directo a WARNING (consistency_check="SKIPPED_BUDGET_EXHAUSTED"), sin llamar al provider. Techo agregado garantizado: budget+TIMEOUT = 140s < 180s, independiente del número de claims. Validación ampliación 2: 26/26 PASS en tests/test_fact_checker.py (2 tests nuevos: degradación por presupuesto agotado con provider.calls verificado, y cota superior del peor caso). Peor caso teórico ahora acotado a 140s con cualquier N de claims | **FIXED + VALIDATED (2026-08-25)** |
| Mezcla de idiomas en chapter_writer: la lógica de construcción de texto tenía ramas `if language` internas y el esqueleto/pie de instrucciones del prompt quedaba fijo en español incluso con language="en" (petición del usuario: eliminar ramas de idioma internas, prefiriendo funciones duplicadas puras por idioma) | `_build_prompt(validated, language="es")` y las funciones deterministas/continuación/fallback concentraban ambas variantes de idioma en una sola función con condicionales; el esqueleto de instrucciones no estaba parametrizado | Refactor en 3 fixes secuenciales: (1) `_build_prompt_es`/`_build_prompt_en` puras con esqueletos íntegros propios por idioma (fix real: EN ya no hereda esqueleto ES) + wrapper delgado de dispatch para compatibilidad; (2) `_elaborate_fact_deterministic`, `_elaborate_fact_pair_deterministic`, `_deterministic_section_paragraphs` separadas en variantes _es/_en puras (las de section_paragraphs llaman a las de fact_deterministic, no a los wrappers); (3) `_build_section_continuation_prompt` y `_fallback_chapter` separadas igual. Verificado por grep: exactamente 6 ocurrencias de `if language` restantes en main.py, TODAS dentro de los 6 wrappers de dispatch — cero ramas de idioma en lógica de construcción de texto | modules/chapter_writer/main.py (PROTECTED, autorización concedida en sesión), tests/test_chapter_writer.py | Tests nuevos: test_build_prompt_en_excludes_es_skeleton, test_build_prompt_es_keeps_es_skeleton; pytest test_chapter_writer.py -k "deterministic" → 6/6 PASS; validación final pytest test_chapter_writer.py + test_chapter_writer_placeholder.py + test_runner_e2e_001.py → 161 passed, 4 failed, 1 skipped (234.42s), E2E runner 100% PASS. Los 4 fallos son constantes en los 3 fixes, preexistentes (tests stale de §17 #19, confirmados también sobre código prístino vía git stash) | **FIXED + VALIDATED (2026-08-25)** |
| api_book_detail (frontend_api.py) usaba .get() sobre sqlite3.Row (no soportado), lanzando AttributeError al pedir detalle de un libro con capítulos reales | sqlite3.Row no implementa .get(); descubierto durante el test nuevo de borrado de libros | Conversión explícita de filas a dict antes de acceder con .get() | frontend/frontend_api.py | cubierto por tests/test_frontend_api_delete_book.py y regresión en tests/test_frontend_api.py (54 passed en total) | **FIXED + VALIDATED** |
| Fabricación de hechos históricos falsos con apariencia factual entregada en el DOCX sin bloqueo (§17 #20, evidencia real book_59 "Historia Completa del Genocidio en Palestina" cap.2): outline exigía secciones "campamentos de concentración en Palestina" sin base en ninguna fuente; writer LLM (execution_mode=real) inventó Eichmann, campos 1942-1948 y 50.000-100.000 víctimas con research=null y fuentes de 148-1.373 chars que no lo mencionaban; fact_checker las clasificó WARNING y quality_gate=PASS pese a status=FAIL | 3 capas: (1) fact_checker no distinguía "sin soporte" de "fabricación con especificidad verificable" y su gate solo fallaba por research_required/texto insuficiente; (2) chapter_writer ante fuentes insuficientes no tenía instrucción de generalizar; (3) la fase outline nunca recibía las fuentes reales (build_payload outline sin sources) aunque ya existieran tras research | (1) fact_checker: _has_fabrication_signature() (fecha+cifra+nombre propio o bigrama propio compuesto) + _is_unsupported_issue() + _escalate_fabrication_issue() → ERROR + quality_gate=FAIL con cualquier ERROR. (2) chapter_writer: regla anti-invención ES/EN en _build_prompt. (3) BookPlanPayload.sources opcional (schemas.py) + build_payload outline pasa fuentes resumidas (título+300 chars) por idioma + REGLA DE ANCLAJE A FUENTES en _build_prompt del planner (sin sources, prompt idéntico al anterior) | modules/fact_checker/main.py, modules/chapter_writer/main.py (PROTECTED), modules/book_planner/main.py, core/schemas.py, frontend/editorial.py | 12 tests nuevos: test_fabrication_signature_detection, test_book59_fabricated_claims_escalate_to_error_and_fail_gate, test_supported_specific_claim_not_escalated (tests/test_fact_checker.py); test_build_prompt_anti_fabrication_rule_both_languages (test_chapter_writer.py); 3 en test_book_planner.py (incluye reproducción book_59); 2 en test_autopilot_research_sources.py (outline payload). Checkpoint suite completa 2026-08-24: **685 passed, 4 failed (idénticos preexistentes §17 #19), 1 skipped, 131.49s — sin regresión** | **FIXED + VALIDATED** |
| quality_gate._check_images comparaba contra literal hardcodeado 3 en vez de books.image_count real — cualquier libro con image_count≠3 fallaba el gate de imágenes aunque el pipeline hubiera entregado exactamente las imágenes pedidas (evidencia real: libro 38, image_count=5, 5 imágenes/capítulo reales, QC FAIL "Imágenes por capítulo != 3") | 3 literales hardcodeados en modules/quality_control/main.py (_check_images, líneas ~419/425/432), nunca leía book.image_count | expected = clamp(book.image_count or 3, 0, 20); comparación y mensajes usan expected en vez de literal 3 | modules/quality_control/main.py, modules/quality_control/tests/test_quality_control.py | test_check_images_uses_book_image_count_not_literal (nuevo) + modules/quality_control/tests/ 8/8 PASS + tests/test_quality_gates.py 14/15 PASS (1 fallo preexistente y ajeno, ver deuda nueva §19); CONFIRMADO END-TO-END EN PRODUCCIÓN REAL 2026-08-22: retry de book_38 tras reinicio limpio → quality_gate check de imágenes "5 imágenes por capítulo" (PASS, antes '!=3'), job status=COMPLETED, output/docx/book_38_es.docx generado (7.505.053 bytes, 23:12:35). | **FIXED + VALIDATED** |

| Fuentes contaminadas temáticamente (ej. 'Latveria'/Marvel Comics en id 532) se reciclaban indefinidamente a libros futuros ajenos al tema vía add_source (dedupe por url_hash), sin volver a pasar por ningún filtro de anclaje — el filtro de research_web solo corre en la inserción original, nunca en la re-asociación. Evidencia real: book_39 'Historia del Mítico Juego Doom' heredó Latveria (insertada 2026-08-16 para book_23, antes de existir el fix de anclaje multi-palabra). | SourceManager.add_source reutiliza la fila existente y hace unión de chapter_ids sin re-validar contenido contra el topic del libro destino (core/book/source_manager.py, líneas 46-69). | 2 helpers nuevos en source_manager.py (get_source_by_url, book_ids_for_source); en core/autopilot.py, antes de add_source: si la fuente ya existe Y pertenece a otro book_id, se re-valida con _has_anchor_keyword (importado de modules/research/main.py, sin modificarlo) contra el topic/título del libro nuevo; si falla, no se asocia. Fuentes nuevas o ya del mismo libro: sin cambio de comportamiento. | core/book/source_manager.py, core/autopilot.py, tests/test_autopilot_editorial.py (test nuevo) | test_stale_source_not_reassociated_without_topic_anchor (nuevo, reproduce el caso real Latveria) + 46/46 PASS (test_autopilot_editorial.py, test_source_manager.py, test_autopilot_research_sources.py). Pendiente de confirmación en producción real tras reinicio del servidor. Confirmación E2E real con control positivo/negativo: book_42 (topic idéntico a book_40, colisión natural por url_hash) — 6 fuentes Doom legítimas SÍ reasociadas, 2 fuentes Marvel stale (Latveria id=640, Pantera Negra id=641) NO reasociadas. Confirmación en producción 2026-08-23: ejercicio directo de la rama de rechazo (core/autopilot.py, cierre de fase research) contra datos reales — fuente id 640 (Latveria, url es.wikipedia.org/wiki/Latveria) propuesta deliberadamente para book_id=45 ('La Saga Doom: Historia del FPS...'); WARNING emitido: 'Fuente 640 (Latveria) NO asociada a book 45: sin anclaje temático (_has_anchor_keyword=False, libros previos [40])'; sources.id=640.chapter_ids sin cambios ([176,177,178]), book_45 solo asociado a sus 8 fuentes legítimas (663-670). Verificación hecha contra copia temporal de la BD (SPACE_LAIR_DB_PATH), producción real intacta. Script usado y archivado en tools/dev/archive/verify_anti_recycling_prod.py. | FIXED + VALIDATED + CONFIRMADO EN PRODUCCIÓN (2026-08-23) |
| Research monolingüe (siempre ES) en libros bilingües/EN: la fase research consultaba solo es.wikipedia.org con hosts hardcodeados y entregaba las MISMAS fuentes ES a writer ES y writer EN — el backstop determinista EN envolvía hechos en español con plantillas inglesas ("Developing this idea, it should be noted that La historia del café...") y el prompt LLM EN recibía research no-nativo. Evidencia real: book_56 ('La historia del café', languages="es,en", 2026-08-24; ambos writers cayeron a execution_mode=deterministic por caída de Ollama y el DOCX EN quedó con hechos ES literales). Cierre de la deuda §19 P3. | `WIKI_BASE`/`WIKI_REST_BASE` hardcodeados a es.wikipedia.org en modules/research/main.py; el parámetro `language` de research_web() solo llegaba a la curación LLM, nunca al fetching; core/autopilot.py ejecutaba la fase research UNA sola vez global y job.data.sources era único para todos los idiomas | Capa fetch de research parametrizada: `_wiki_search/_wiki_extract/_wiki_rest_summary/_backend_wikipedia/_backend_wikidata` aceptan `language` (hosts es/en.wikipedia.org, retrocompatibles vía constantes históricas); `research_web()` propaga el idioma al multi-fuente; `execute()` lo lee del payload. core/autopilot.py: `_run_research_multilang` — libros bilingües ejecutan research UNA vez POR IDIOMA (red + curación LLM), fuentes fusionadas y deduplicadas por URL en job.data.sources (shape histórico intacto para la asociación SourceManager) y desglose por idioma en job.data.sources_by_lang; libros monolingües "en" ya consultan su Wikipedia nativa (regresión cero en "es"). frontend/editorial.py: payload research lleva el idioma activo; build_payload(writer/writer_en) selecciona sources_by_lang[idioma] con fallback histórico. `_deterministic_curate()` NO requirió cambios (ranker puro sobre candidatos que ya llegan en el idioma correcto — cubre ruta LLM Y backstop determinista). chapter_writer/document_builder sin cambios | modules/research/main.py, core/autopilot.py, frontend/editorial.py | test_g_bilingual_book_runs_research_once_per_language + test_h_monolingual_es_book_still_single_research_call (nuevos, tests/test_autopilot_research_sources.py, patrón executor real default_executor_factory): bilingüe → exactamente 2 llamadas (1 por idioma), fuentes separadas sin mezcla ni duplicados (6 únicas); mono 'es' → 1 sola llamada, shape histórico intacto. 8/8 PASS en ese archivo (6 preexistentes sin regresión + 2 nuevos). Previo: 28/28 PASS (test_research_multisource.py + test_research_sources.py + test_research_curation.py). Los 4 fallos de test_chapter_writer.py son preexistentes y ajenos (§17 #19) | **FIXED + VALIDATED** |
| Contaminación temática en book_57 por stale-process (caso CERRADO como problema de proceso, NO de código): el DOCX bilingüe llevaba fuentes ajenas al tema 'series virales' — The Backyardigans, Liga Mexicana de Béisbol, Historia de la biología, El Chombo (todas es.wikipedia, ids 712/714/715/716) | El proceso que sirvió el job (19:37–20:03) tenía el orquestador multi-idioma nuevo activo pero un módulo research en memoria ANTERIOR al fix (mezcla de versiones; archivos del fix escritos 20:49–20:50). El _has_anchor_keyword vigente rechaza las 4 ofensoras cuando se ejecuta contra el topic real (verificado manualmente: pasa=False en las 4) | Sin cambio de código: reinicio limpio del servidor (PID 20360, START 21:33:32, posterior a los LWTs) + validación end-to-end con book_58 ('Curiosidades sobre los pulpos', es,en, 1 capítulo): COMPLETED sin errores y 0 fuentes fuera de tema. book_57 se conserva intacto como evidencia histórica del patrón (mismo criterio que book_37/book_39). Detalle del patrón: §19 P2 cuarta ocurrencia | core/autopilot.py + modules/research/main.py + frontend/editorial.py (solo timestamps, sin reescritura); datos: BD producción | Diagnóstico previo solo-lectura (ejecución manual de _has_anchor_keyword vs topic real, 4/4 False) + validación Frente A con book_58: pasada ES limpia (8/8 fuentes on-topic), filtro de anclaje operativo con código fresco. Gap residual separado como deuda nueva en §19 (pasada EN sin contenido nativo inglés) | **FIXED + VALIDATED (resolución operativa: reinicio; sin fix de código necesario)** |
| _run_image_gen_split compensaba el shortfall de búsqueda web (image_search_ratio>0) generando SIEMPRE imágenes IA vía generate_chapter_images hasta cubrir num_images completo, ignorando el ratio configurado por el usuario — un libro con ratio=0.75 (75% web) terminó con 65% de imágenes IA reales; con ratio=1.0 (100% web) el sistema podía generar IA igualmente si la búsqueda web fallaba. Evidencia real: libro 65, ratio=0.75 en BD, 11 imágenes IA vs 6 web (contra lo esperado ~1 IA vs 2 web por capítulo) | La rama de compensación de shortfall no conocía el ratio; lanzaba una tarea de generate_chapter_images por el déficit completo sin ningún tope | Cuota máxima de IA por capítulo derivada del ratio: max_ia_quota = round(num_images * (1 - ratio)); se descuenta el n_generate ya usado en el split inicial; la compensación solo genera hasta agotar la cuota restante (puede ser 0), dejando el resto del shortfall sin cubrir con WARNING logueado ("[fix ratio] image_gen_split: shortfall=N no cubierto completo por cuota IA agotada...") en vez de inflar la proporción de IA. Dedup por image_path y split inicial n_search/n_generate SIN CAMBIOS. Con ratio=0.0 comportamiento sin cambio (passthrough a _run_single) | core/autopilot.py (_run_image_gen_split, autorizado), tests/test_autopilot_persist_chapters.py | 7/7 PASS en tests/test_autopilot_persist_chapters.py: 2 tests existentes actualizados al contrato nuevo (test_image_gen_split_compensates_shortfall con ratio 0.75→0.5 para ejercitar compensación parcial; test_image_gen_split_dedupes_preexisting_duplicate_before_shortfall con ratio=0.75 y cuota agotada → sin tercera task) + 1 test nuevo test_image_gen_split_ratio_one_never_compensates_with_generation (ratio=1.0, búsqueda incompleta, 0 llamadas a generate_chapter_images, capítulo queda con menos imágenes de las pedidas) | **FIXED + VALIDATED (2026-08-25)** |
| §17 #23 (parcial): backstop determinista EN de chapter_writer podía recibir contenido en español por dos vías en frontend/editorial.py (no en chapter_writer, que no fue tocado): (i) el campo 'objective' del payload writer_en usaba siempre la descripción en ESPAÑOL del libro, nunca description_en; (ii) cuando sources_by_lang/research_by_lang no tenían desglose para 'en' (jobs antiguos o research sin split), el payload EN caía por fallback al dato ES compartido, alimentando snippets españoles verbatim a _extract_research_facts (función compartida, sin chequeo de idioma, main.py:1301-1332, sin cambios) | editorial.py::build_phase_payload no distinguía idioma para el campo objective (línea ~693) ni evitaba el fallback cruzado ES→EN cuando el desglose por idioma estaba vacío (líneas ~696-697) | En editorial.py (NO protegido): (1) objective para libros EN usa book.description_en o None, nunca la descripción ES; (2) para libros EN se eliminó el fallback a sources/research ES cuando sources_by_lang['en']/research_by_lang['en'] están vacíos — se pasa vacío en su lugar (el backstop EN ya generaliza correctamente sin hechos, verificado antes del fix). Libros ES sin cambio de comportamiento. modules/chapter_writer/main.py (PROTECTED) NO fue modificado | frontend/editorial.py, tests/test_editorial_bilingual_plan.py | Verificación previa (solo lectura) confirmó que _deterministic_section_paragraphs_en y _fallback_chapter_en degradan correctamente a párrafos genéricos en inglés con facts=[]/objective=None, sin excepciones ni placeholders. 2 tests nuevos + 5 preexistentes → 7/7 PASS en tests/test_editorial_bilingual_plan.py | **FIXED + VALIDATED (2026-08-25) — PARCIAL, ver §17 #23 actualizado** |
| §17 #24: image_search insertaba imágenes con texto real en español y marca de terceros visible en libros EN (evidencia book_63_en.pdf: marca de agua 'MUNDOENTRENAMIENTO.COM' + texto español, portada de revista con 'FÍSICA'). Causa triple: (i) chapter_title del payload de image_plan/image_gen usaba siempre chapters.title (ES), nunca title_en, alimentando la query de búsqueda con texto español; (ii) image_search no propagaba el campo language a la request real de SearXNG (solo a metadata), sin filtrar resultados por idioma; (iii) image_planner._build_prompt tenía TODAS las instrucciones al LLM hardcodeadas en español, sin variante EN, para el prompt de generación IA (SDXL) | frontend/editorial.py no aplicaba el criterio bilingüe ya existente (title_en, fix §17 #21) al chapter_title de image_plan/image_gen; modules/image_search/main.py leía language solo para metadata; modules/image_planner/main.py no tenía variante _en de _build_prompt (a diferencia del refactor ya hecho en chapter_writer, §16 2026-08-25) | (1) editorial.py: chapter_title de image_plan/image_gen usa title_en cuando el libro es EN+bilingüe y no es NULL, con fallback a title ES — mismo criterio que writer_en; autopilot.py hereda el fix vía build_phase_payload sin tocarlo. (2) image_search (OUT_OF_SCOPE, autorizado): _searxng_search acepta parámetro language; para libros EN añade language=en a la request real de SearXNG; ES/ausente mantiene comportamiento histórico sin filtro; denylist §17 #5 y anclaje §17 #11 intactos. (3) image_planner (OUT_OF_SCOPE, autorizado): _build_prompt dividido en _build_prompt_es (idéntico al anterior) + _build_prompt_en (mismo esqueleto en inglés, regla de 'sin texto/marcas' reforzada explícitamente); wrapper de dispatch por payload.language, mismo patrón que el refactor de chapter_writer (§16 2026-08-25) | frontend/editorial.py, modules/image_search/main.py, modules/image_planner/main.py, tests/test_editorial_bilingual_plan.py, modules/image_search/tests/test_image_search.py, tests/test_image_planner.py | 8/8 PASS test_editorial_bilingual_plan.py (1 nuevo); 10/10 PASS modules/image_search/tests/test_image_search.py (1 nuevo); 25/25 PASS tests/test_image_planner.py (2 nuevos). Sin regresión en denylist/anclaje/fallback plan existentes | **FIXED + VALIDATED (2026-08-25)** |
| Job bilingüe abortaba COMPLETO cuando la pasada research del idioma secundario tenía pocas fuentes (evidencia book_62 'Alimentacion sana y ejercicios para una vida longeva', languages="es,en", 2026-08-25: 4 tasks research "en" fallidas con source_count=1-2 < min_sources=3 — query/topic en español sin traducir → filtro de anclaje descarta casi todos los candidatos ingleses; la pasada "es" sí pasó con fuentes de sobra) | `core/autopilot.py::_run_research_multilang` abortaba el job ante CUALQUIER fallo de cualquier idioma (`if not res.ok: return ok=False`), sin fallback ni distinción entre idioma primario/secundario ni tipo de fallo | Fallback focalizado SOLO en el caso exacto book_62: si la pasada del idioma NO primario falla específicamente por gate de source_count insuficiente (misma condición que construye `_run_single` para research: error con prefijo `research#... source_count=N (min=M)`), la fase NO aborta: `job.data.sources_by_lang[secundario] = copia de sources_by_lang[primario]`, warning registrado vía log WARNING + clave `warnings` en las métricas de fase, `per_language_status[lang]="FALLBACK"`, fase PASS. La lista global `job.data.sources` NO se toca. Fallo del idioma PRIMARIO o cualquier otro fallo (excepción/timeout/error real) → comportamiento anterior sin cambio (hard fail) | core/autopilot.py (`_run_research_multilang`), tests/test_autopilot_research_sources.py | test_i_bilingual_secondary_lang_low_source_count_falls_back (nuevo, reproduce book_62 con stub: 'es' PASS 5 fuentes / 'en' FAIL gate source_count=1): 11/11 PASS en tests/test_autopilot_research_sources.py (pytest tests/test_autopilot_research_sources.py -v, 2.66s). Regresión confirmada en verde: test_g (bilingüe normal) y test_h (monolingüe) intactos; cobertura existente del aborto por fallo primario no duplicada. book_62 se conserva INTACTO como evidencia (no se reintenta ni regenera), mismo criterio que book_37/39/57 | **FIXED + VALIDATED** |
| Plan editorial monolingüe en libros bilingües (§17 #21, evidencia book_62_edición_EN): título de libro/descripción/títulos de capítulo/headings de sección generados UNA vez solo en español y compartidos con la edición EN sin traducción → TOC/headings/captions "Chapter N: Alimentacion sana..." en español, subcabeceras Introducción/Desarrollo 20/20 españolas (Conclusión mezclada 12/8 por `_canonicalize_headings`), gibberish hispano en imágenes ComfyUI (prompts con título/texto ES) | El planner es fase global sin idioma por edición: payload llega con `language="es,en"` crudo, prompt íntegramente español, plan persistido una vez en chapters.title/outline; writer EN/document_builder/image_planner/image_generator consumen ese plan tal cual (verificado: ninguno lee BD propia ni traduce) | Opción A (plan bilingüe completo): (1) migración DB idempotente `books.title_en/description_en` + `chapters.title_en/outline_en`; (2) book_planner: si `_plan_languages(language)>1`, UNA llamada LLM extra tras el plan ES traduce libro+capítulos en un solo JSON con validación all-or-nothing (alineación índice a índice, cadenas no vacías, rechazo byte-idéntico al ES); fallback determinista SIN LLM extra pero mapea outline_en canónico (Introducción→Introduction etc.); timeout interno propio PLANNER_TRANSLATE_TIMEOUT=60s + module.json 120→160s (mismo margen relativo que research FASE 8M.2-fix); (3) editorial: build_payload writer EN y _build_book_dict seleccionan _en cuando bilingüe+EN+no NULL, fallback ES explícito con log INFO; autopilot propaga/persiste los campos (aditivo, mismo wiring histórico). document_builder/chapter_writer/image_planner/image_generator SIN CAMBIOS. `_resolve_book_languages` movida a editorial.py con alias compatible en autopilot | core/database.py, modules/book_planner/main.py + module.json (OUT_OF_SCOPE, autorizado), frontend/editorial.py, core/autopilot.py (aditivo), tests/test_book_planner.py, tests/test_editorial_bilingual_plan.py (nuevo) | 11 tests nuevos (j-p planner + 5 editorial) → **65/65 PASS** (pytest test_book_planner.py + test_editorial_bilingual_plan.py, 1.39s); regresión focalizada del movimiento de _resolve_book_languages: **47/47 PASS** (test_autopilot_research_sources/document_output/editorial/writer_en); humo offline real (BD temporal aislada, payload exacto book_62, Ollama activo): plan principal con JSON truncado a 20 caps → fallback determinista por diseño, outline_en 20/20 vía mapeo canónico, title_en/description_en=None correctos; py_compile OK. Los 6 libros bilingües existentes (56-60, 62) quedan con campos _en NULL por diseño (no se retro-traducen). book_62/PDF intactos como evidencia | **FIXED + VALIDATED** |
| Denylist de dominios ausente en image_search: podían insertarse en el DOCX portadas de editoriales reales (McGraw-Hill, LALEO) y documentos/diapositivas de terceros (Scribd, material docente universitario) con logos/atribución visibles (ver §17 #5 para detalle completo) | El módulo consumía solo img_src/engine/title de cada resultado de SearXNG y descartaba la URL de la página fuente; sin ningún filtro de dominio ni de tipo de contenido | `_DOMAIN_DENYLIST` (11 dominios) + helper `_is_denylisted()` (netloc sin `www.`, comparación por contenido para cubrir subdominios); en el bucle principal se comprueba img_src Y url/parsed_url (página fuente) ANTES de descargar; skip-and-continue sin ocupar slot ni error-slot. Resumen corto — ver §17 #5 para detalle completo | modules/image_search/main.py, modules/image_search/tests/test_image_search.py | test_denylist_bloquea_dominio_y_no_ocupa_slot (nuevo) + suite del módulo 7/7 PASS (modules/image_search/tests/test_image_search.py); py_compile OK | **FIXED + VALIDATED** |
| 3 tests en tests/test_quality_gates.py (incl. test_research_with_sources_passes) daban falsa confianza: los fakes de research_web tenían firma obsoleta (sin language/topic), causando TypeError capturado silenciosamente y verificando la rama de excepción de execute() en vez de la lógica real del gate de research (ver §19 P2) | Deriva de contrato tras el refactor multi-fuente/anclaje de research_web (añadió kwargs language, topic) | Firma de los 3 fake_research actualizada a (query, max_sources=8, timeout=20, language="es", topic=None); cuerpos y assertions intactos. modules/research/main.py no tocado | tests/test_quality_gates.py | pytest tests/test_quality_gates.py -k research -v -s → 3 passed; verificado por log que los 3 corren con modo="real" (no "failed"), confirmando que ejercitan la lógica real del gate | **FIXED + VALIDATED** |
| Sección "Fuentes utilizadas" del DOCX se renderizaba parseando literalmente el texto libre escrito por el LLM del writer (`## Fuentes` dentro de edited_es/draft_es) en vez de usar los datos reales de `chapters.sources` — causaba fuentes duplicadas (#9), citas bibliográficas APA completas fabricadas por el LLM (#13, evidencia book_47 cap2/ch192: 3 citas inexistentes), y secciones omitidas cuando el LLM no generaba el heading (#14, evidencia book_46 cap2/ch189 sin cola, cap3/ch190 con fuente off-topic) | document_builder/_add_chapter parseaba `_split_sources_tail` sobre el texto del capítulo como única fuente del listado; nunca consultaba `chapters.sources`/SourceManager. El prompt del writer (chapter_writer/main.py:1086) ordena generar esa sección, contradiciendo su propia línea 1108 ("si el pipeline ya añade la sección, no generes una segunda") que asumía un fallback en document_builder que nunca existió | `_split_sources_tail` ampliado para detectar y descartar cualquier cola del LLM (headings "fuentes utilizadas"/"referencias"/"bibliografía"/"fuentes" a secas, nunca renderizada); nueva construcción determinista de "## Fuentes utilizadas" desde `chapter.sources` (list[str] de URLs, formato `- <url>` con hipervínculo real vía `_add_hyperlink`); sección omitida si `chapter.sources` está vacío, sin placeholder | modules/document_builder/main.py (`_split_sources_tail`, `_add_chapter`), modules/document_builder/tests/test_document_builder.py (3 tests actualizados + 4 nuevos: `test_sources_section_from_chapter_sources_without_llm_tail`, `test_empty_chapter_sources_omits_section_no_placeholder`, `test_fabricated_llm_tail_replaced_by_real_sources`, `test_duplicate_source_lines_discarded`) | 22/22 PASS en modules/document_builder/tests/test_document_builder.py (2.65s); grep confirmó que ningún otro test en tests/ dependía del comportamiento antiguo; CONFIRMADO EN PRODUCCIÓN REAL con book_48 ('Los Mejores Grupos de Rock Español', 3 capítulos): 3/3 secciones 'Fuentes utilizadas' con URLs reales, hipervínculos funcionales, sin duplicados ni fabricación; header/footer/copyright (A1/A2/A3) verificados visualmente correctos en el mismo documento | **FIXED + VALIDATED (mitigación de producto — no corrige la causa en el writer, ver nota residual en §17 #13)** |



> **NOTA libros de validación (2026-08-22):** `book_41` ("Historia del videojuego Doom y su legado") y `book_42` ("Historia del Mítico Juego Doom") son LIBROS DE VALIDACIÓN creados deliberadamente vía API para probar en producción real el fix anti-reciclaje de fuentes (book_42, control positivo/negativo) y el dedupe de claims de fact_checker (book_41). NO son libros de producción reales — no confundir al revisar la BD (`data/space_lair.db`). Se conservan intactos como evidencia.

| Split image_search/image_generator por ratio no existía (books.image_search_ratio sin consumidor) | Feature nueva, no bug | _run_image_gen_split en core/autopilot.py: con ratio>0 encola 2 tasks por capítulo (search_chapter_images + generate_chapter_images), reparte num_images proporcionalmente, fusiona results; ratio=0.0/None = passthrough exacto a _run_single (sin cambio de comportamiento) | core/autopilot.py, tests/test_autopilot_persist_chapters.py | test_image_gen_ratio_zero_delegates_to_run_single (passthrough) + test_image_gen_ratio_positive_splits_into_two_tasks (2 tasks, reparto 2+2=4, results fusionados, chapters.images poblado con 4 rutas reales) — 4/4 PASS | **IMPLEMENTED + VALIDATED + CONFIRMADO OPERATIVO EN PRODUCCIÓN (2026-08-23, evidencia book_44, ver §23)** |
| search_chapter_images sin PAYLOAD_SCHEMAS/OUTPUT_SCHEMAS en core/schemas.py (P1 §19) | Módulo nuevo (8N image_search) sin registrar en el mapeo de validación central; bloqueaba el uso real de books.image_search_ratio>0 en producción (la task fallaría en validate_payload) | Nueva clase `ImageSearchPayload(TaskPayload)` en core/schemas.py con los 6 campos reales leídos por modules/image_search/main.py (book_id, chapter_number, language, chapter_title, chapter_text, num_images); registrada en PAYLOAD_SCHEMAS. OUTPUT_SCHEMAS reutiliza ImageGenerateOutput existiente (plug-compatible, sin duplicar clase) | core/schemas.py, tests/test_schemas_image_search.py (nuevo) | 3/3 PASS (test_schemas_image_search.py) + sanity test_autopilot_persist_chapters.py 4 passed in 1.84s + import OK | **FIXED + VALIDATED** |
| Editor timeout (tareas muertas) | LLM lento agotaba timeout scheduler 180s sin fallback | `EDITOR_PROVIDER_TIMEOUT=60`, `EDITOR_MAX_RETRIES=1`, `_fallback_edit` (devuelve original) | modules/editor/main.py | tests/test_editor.py + E2E (fallback) | **FIXED + VALIDATED** |
| Writer timeout + duplicados de continuación | LLM lento + continuaciones repetidas sin control | `WRITER_PROVIDER_TIMEOUT=60`, `WRITER_TOTAL_TIME_BUDGET=150`, `ABSOLUTE_HARD_LIMIT=8`, duplicate guard + backstop determinista | modules/chapter_writer/main.py | test_chapter_writer + E2E (1668w, det.) | **FIXED + VALIDATED** |
| Rechazos del LLM (refusals) aceptados como continuación válida y concatenados al capítulo (book_37.docx pág. 11: "Lo siento, pero no puedo ayudar con eso.") | PLACEHOLDER_PATTERNS no cubría frases de negativa del LLM; una propuesta de continuación que era un rechazo no era duplicado ni vacía, así que se ACEPTABA e insertaba. Autorización explícita del usuario para tocar chapter_writer (PROTECTED) y editor (OUT_OF_SCOPE). | Nuevo set `REFUSAL_PATTERNS` (8 patrones case-insensitive ES/EN) + `_detect_refusal()`; en `_continuation_step` la propuesta se rechaza con status `rejected_refusal`, tratado en el bucle exterior igual que `rejected_duplicate` (se descarta y reintenta si queda presupuesto; NO detiene la fase). En editor, `_detect_refusal()` sobre `edited_text` dispara `_fallback_edit` igual que salida inválida (copia local de la lista para evitar dependencia cruzada). Pendiente: research/main.py (fuera de alcance de este fix). | modules/chapter_writer/main.py, modules/editor/main.py, tests/test_chapter_writer_placeholder.py, tests/test_editor.py | `pytest tests/test_chapter_writer.py tests/test_chapter_writer_placeholder.py tests/test_editor.py -v` → **177 passed** (incluye nuevos: frase real detectada por `_detect_refusal`, `_continuation_step` devuelve `rejected_refusal` sin insertar el texto, y `execute` del editor activa fallback dejando el capítulo idéntico al original) | **FIXED + VALIDATED** |
| Flaky tests 7.9D.7 | `os.environ['CHAP_FORCE_MIN']` en import-time de runner contaminaba pytest | Env movido a `_configure_environment()` en `main()` | run_e2e_001_editorial.py | 528 passed en tests/ | **FIXED + VALIDATED** |
| DOCX `comments` >255 chars → ValueError | Librería python-docx limita a 255 | `comments=(description or "")[:255]` | modules/document_builder/main.py | test + E2E PASS | **FIXED + VALIDATED** |
| DOCX filename colisión cross-book | Nombre `book_es.docx` sin book_id | `book_{book_id}_{language}.docx` | modules/document_builder/main.py | test_e2e_docx_integration (8E.6/8E.7) | **FIXED + VALIDATED** |
| Metadata no llegaba a QC (FAIL) | UI enviaba solo title+idea; idea no → description | `create_book` mapea `idea`→`description`; preserva metadata explícita | frontend/editorial.py | test_editorial_metadata (8E.2) | **FIXED + VALIDATED** |
| Fuentes globales no se propagaban (Q gate #3) | QC usaba fuentes de job en vez de asociaciones reales | `_chapter_source_urls()` desde SourceManager; `_build_book_dict` incluye `sources` por capítulo | frontend/editorial.py | test_editorial_sources (8D.2) | **FIXED + VALIDATED** |
| Umbrales QC usaban defaults 20/30/40 | build_phase_payload no pasaba min/target/max del libro | `build_phase_payload` propaga min/target/max reales | core/autopilot.py | test_autopilot_quality_gate_payload (8E.1) | **FIXED + VALIDATED** |
| Título de capítulo duplicado en DOCX | Speaker/LLM duplicaba heading | parseo controlado de markdown + headings canónicos | modules/document_builder/main.py | tests | **FIXED + VALIDATED** |
| Metadata opcional (author/genre/target_audience) bloqueaba Quality Gate + placeholder "Autor: Autor" en DOCX legal | QC exigía bool(author)/bool(genre) como obligatorios pese a ser opcionales en la UI (frontend/editorial.py nunca los inventa); document_builder usaba fallback genérico "Autor" | quality_control/main.py: author/genre/target_audience bajan de FAIL a WARNING en _check_book (title+description siguen obligatorios); document_builder/main.py: _add_legal omite la línea "Autor: X" si no hay autor y usa book.title en el copyright | modules/quality_control/main.py, modules/document_builder/main.py | test_editorial_metadata.py::test_f_minimal_payload_without_author_genre + test_document_builder.py::test_build_book_docx_legal_without_author_omits_line_and_uses_title + regresión focalizada (test_quality_gates.py 15/15, test_autopilot_quality_gate_payload.py 1/1, test_document_builder.py 12/12) | **FIXED + VALIDATED** |
| chapters.sources quedaba '[]' tras el writer (columna nunca poblada) | chapter_writer no escribía sources; nadie más lo hacía tampoco, pese a que SourceManager ya tenía las asociaciones reales desde la fase research | core/autopilot.py::_persist_chapter (rama writer/writer_en) llama a nuevo helper frontend/editorial.py::persist_chapter_sources, que persiste las URLs reales obtenidas de SourceManager vía _chapter_source_urls. No se tocó chapter_writer/main.py (PROTECTED) ni ningún módulo OUT_OF_SCOPE | core/autopilot.py, frontend/editorial.py | test_writer_populates_chapters_sources_in_db (nuevo) + regresión focalizada (test_autopilot_document_output.py 6/6, test_autopilot_editorial.py 23/23) | **FIXED + VALIDATED** |
| Frontend solo mostraba 8/10 fases del pipeline (image_plan/image_gen ausentes en AUTOPILOT_PHASES de app.js, pese a que el backend ya las emitía por SSE) | app.js tenía su propio array AUTOPILOT_PHASES desincronizado del real en core/autopilot.py; nunca se actualizó al añadirse las fases de imagen | Se añadieron las 2 fases al array de app.js en la posición exacta del backend (entre editor y quality_gate), más un icono SVG nuevo 'image' en workerToolSvg. index.html no requirió cambios (renderizado 100% dinámico vía renderLivingPipeline) | frontend/app.js | Comparación línea a línea de ambos arrays (backend vs frontend, orden idéntico confirmado) + test_frontend_api_autopilot.py 17/17 PASS + node --check (sintaxis OK) | **FIXED + VALIDATED** |
| Bug `or 3` ignoraba `images_per_chapter=0` (5 puntos: frontend/frontend_api.py:729, frontend/editorial.py:399/542/554, image_generator/main.py:172/265) | el patrón `data.get("num_images") or 3` (o equivalente) convertía 0 en 3 | se sustituyó por comprobación explícita de `None` (solo ausente → 3; 0 se conserva); `image_planner` no se tocó (ya manejaba 0) | frontend/frontend_api.py, frontend/editorial.py, modules/image_generator/main.py | test nuevo test_build_payload_preserves_num_images_zero (editorial build_payload: image_plan/image_gen 0→0, ausente→3) + comprobación manual generate_chapter_images (0→0, ausente→3) + regresión focalizada 60 PASS (test_image_generator.py, test_editorial_panel.py, test_frontend_api_autopilot.py 17/17, test_image_planner.py) | **FIXED + VALIDATED** |
| Sin cobertura de orquestación real para editor/image_gen en `_persist_chapter` | El único test de orquestación real cubría solo la rama writer/writer_en (persist_chapters/sources); editor e image_gen solo tenían tests de módulo aislado o payload-only con harness (test_autopilot_editorial.py), sin ejecutar `_persist_chapter` vía scheduler | tests/test_autopilot_persist_chapters.py (nuevo) copia el patrón del test writer: executor real (default_executor_factory + scheduler) + módulo editor STUB + módulo REAL image_generator (LocalImageProvider, sin LLM); verifica que `_persist_chapter` persiste edited_es e images reales en BD | tests/test_autopilot_persist_chapters.py (nuevo); NO se editó core/autopilot.py ni ningún módulo | test_autopilot_persist_chapters.py 2/2 PASS + sanity test_autopilot_document_output.py 6/6 PASS (mismo patrón reutilizado) | **FIXED + VALIDATED** |



| Textos de interfaz en inglés (labels de fases del pipeline, banner 'BOOK READY', métricas, conteo de fases con enum crudo) | Cadenas sin traducir + líneas 424-428 usaban valores crudos del enum en vez del mapeo PHASE_STATUS_LABEL ya existente | 10 labels de fases + banner + métricas + card traducidos; 424-428 ahora usan PHASE_STATUS_LABEL para consistencia con el resto de la UI | frontend/app.js | node --check (sintaxis OK) + comparación manual de cadenas (grep) confirmando ausencia de las cadenas en inglés originales | **FIXED + VALIDATED** |
| Código muerto/redundante en frontend_api.py (import Any sin uso, posixpath muerto, import os local redundante, import load_book duplicado, comentario 'Workflow Endpoints' repetido, indentación de comentario anómala) | Acumulación de imports y comentarios sin limpiar; microdiagnóstico confirmó que NO había código inaccesible ni rutas Flask duplicadas (todas las 31 rutas y funciones a nivel módulo están referenciadas) | 5 limpiezas puntuales aplicadas: Any eliminado de imports, posixpath y os locales eliminados de api_book_docx, load_book local redundante eliminado (se usa el import global), comentario duplicado eliminado, indentación corregida | frontend/frontend_api.py | ast.parse OK + pytest tests/test_frontend_api*.py 48/48 PASS | **FIXED + VALIDATED** |
| Gap de orquestación: la fase research nunca traducía su resultado real (status=FAIL/quality_gate=FAIL/source_count<min_sources) a fallo de fase; _run_single solo lo hacía para quality_gate y fact_check | research quedaba marcada PASS con solo que la tarea terminara sin excepción, aunque sus propias metrics reportaran FAIL. Afectó a 4/9 libros observados (books 8, 9, 16, 17) — el writer escribía sin fuentes reales | core/autopilot.py::_run_single ahora traduce también la fase research al mismo patrón ya usado en quality_gate/fact_check (gate_fail si status/quality_gate=FAIL o source_count < min_sources) | core/autopilot.py | 58/58 tests focalizados (test_autopilot*.py) PASS, sin bajar ningún requisito | **FIXED + VALIDATED** |
| Calidad insuficiente de research: solo consultaba Wikipedia en español, fallando por completo en temas sin cobertura (ej. videojuegos recientes), forzando al writer a escribir sin material real | modules/research/main.py solo implementaba _wiki_search/_wiki_extract acoplados a es.wikipedia.org, sin fuentes alternativas ni fallback de idioma | Autorización explícita del usuario (módulo OUT_OF_SCOPE) para implementar búsqueda multi-fuente (Wikipedia es→en + Wikidata, archive.org disponible pero deshabilitado por defecto) + curación opcional con LLM (RESEARCH_USE_LLM, timeout/budget acotados igual que writer/editor: RESEARCH_PROVIDER_TIMEOUT=40, RESEARCH_TOTAL_TIME_BUDGET=90) con fallback determinista _deterministic_curate() y validación anti-alucinación de URLs (descarta URLs inventadas por el LLM que no estén en los candidatos reales). DDG Instant Answer y GDELT evaluados y descartados (poco valor / rate_limited) | modules/research/main.py, tests/test_research_multisource.py (nuevo), tests/test_research_curation.py (nuevo), tests/test_research_sources.py (nuevo) | 22/22 tests específicos PASS + 37/37 incluyendo test_quality_gates.py; shape de retorno consumido por autopilot sin cambios; gate_fail de la Tarea 1 confirmado intacto | **FIXED + VALIDATED (pendiente confirmación en checkpoint de suite completa)** |
| `book_planner.execute` no emitía `language`/`genre` en su salida; `author` no derivable honestamente de la idea | `modules/book_planner/main.py:execute()` (retorno) construía el dict sin `language` ni `genre`; `BookPlanOutput` (core/schemas.py:112-119) no los declaraba; `BookPlanPayload` tenía `language` (default "es") pero no se propagaba | `execute()` ahora incluye `language` (del payload, default "es") y `genre` (inferido keyword-based desde la `idea`; None si no hay keyword claro); `_fallback_plan` también emitidos; `author` intencionalmente omitido (no hay dato real) | `modules/book_planner/main.py` | 53/53 PASS (test_book_planner.py + test_editorial_metadata.py + test_autopilot_quality_gate_payload.py) | **FIXED + VALIDATED** |

| outline.sections vacío en book_planner (FASE 1/outline) | El prompt del LLM del planner no solicitaba el campo `sections` por capítulo y no existía fallback determinista (a diferencia de writer/editor); el outline con `sections=None`/`[]` provocaba NO_TARGET_SECTION en el writer y falla del mínimo de palabras | (1) `_build_prompt` exige `sections` (heading + objective) por capítulo; (2) `_DEFAULT_SECTION_HEADINGS`, `_default_sections()` y `_ensure_sections()` proveen fallback determinista; (3) `_normalize_plan` aplica `_ensure_sections` a cada capítulo | `modules/book_planner/main.py` | 49/49 PASS en `tests/test_book_planner.py` (3 tests nuevos). Suite completa: 596 passed, 0 failed, 0 errors | **FIXED + VALIDATED** |
| Falso positivo en gate de relevancia research (FASE 8K.3): `_keyword_overlap` usaba substring (`w in haystack`) y no filtraba stopwords; query real `"Los Dooms: El Último"` extraía keywords `['los','dooms','el','último']` y `'el'` coincidía dentro de `"... por el censo."` → overlap=0.250 ≥ umbral 0.15; las 3 fuentes irrelevantes del libro #18 (Crozet, Crimora, Sam Porter Bridges) PASABAN el filtro | Substring matching + ausencia de stopwords ES/EN en keywords de query | (1) Tokenización del haystack en set de palabras (`re.findall` + membership); (2) `_STOPWORDS_ES` con lista mínima ES + EN común; (3) keywords efectivas = palabras ≥2 chars excluyendo stopwords; (4) test `test_real_query_los_dooms_stopwords_filtro` con query/candidatos reales del libro #18 | `modules/research/main.py`, `tests/test_research_sources.py` (nuevo, untracked) | Evidencia real: Crozet/Crimora/Sam Porter Bridges ANTES overlap=0.250 (PASABAN) → DESPUÉS overlap=0.000 (FILTRADAS). Suite completa: **601 passed, 0 failed, 0 errors (142.61s)**. Diagnóstico libro #19: FAIL legítimo (0 candidatos en backends, no culpa del filtro) | **FIXED + VALIDATED** |
| Anchor de relevancia en research insuficiente (después de 8K.3): el filtro por FRACCIÓN de palabras de la query (`_keyword_overlap >= 0.15`) aceptaba fuentes temáticamente ajenas al libro, sobre todo con queries de pocas palabras o con muchas palabras genéricas — 1 sola coincidencia podía superar el umbral 0.15 aunque la fuente no tuviera relación con el tema (evidencia real: book_23 "La Historia de los Dooms" con fuentes sobre Latveria, Kornbluth, Tannehill, Dončić). | El overlap por fracción de palabras de la query no distingue palabra-ancla (tema del libro) de palabras genéricas/modificadoras de capítulo; se agrava con queries cortas pero no se limita a ellas. | Nueva `_has_anchor_keyword(topic, cand)`: exige que al menos una keyword del "topic" (tema del libro, ya viajaba en el payload sin usarse) aparezca como token real en el candidato. Filtro compuesto: `_keyword_overlap >= 0.15 AND _has_anchor_keyword`. `topic=None/""` preserva compatibilidad total (no bloquea). `research_web()` recibe `topic` opcional (retrocompatible); `execute()` lo lee del payload. | modules/research/main.py, tests/test_research_sources.py (3 tests nuevos + 1 fixture ajustada: título "Libro"→"Gato" en `test_source_count_ge_min_keeps_gate_fail_none`, para coherencia temática con la nueva semántica de anclaje) | 25/25 PASS (test_research_sources.py, test_research_multisource.py, test_research_curation.py) | **FIXED + VALIDATED** |
| Gate espurio en fact_check: la fase del Autopilot se marcaba FAILED aunque el módulo devolviera `quality_gate=PASS`, siempre que `status=FAIL` (hallazgo de 1+ claim con severidad ERROR). Afectó 5 libros reales en producción (books 9, 18, 19, 23, 25) con job FAILED espurio. | `core/autopilot.py::_run_single`, bloque fact_check, usaba `if st=="FAIL" or qg=="FAIL"`. `modules/fact_checker/main.py` distingue `status` (hallazgo de claims, informativo) de `quality_gate` (integridad del proceso, el gate real); el propio módulo ya refleja esa jerarquía (`quality_gate=FAIL` eleva `status`, nunca al revés). El bloque research (mismo patrón OR) SÍ es correcto por diseño de 8H.3 y no se tocó. | Condición cambiada a `if qg=="FAIL"` únicamente; `st` se conserva solo en el mensaje de error para trazabilidad. | core/autopilot.py (bloque fact_check únicamente), tests/test_autopilot_fact_check_gate.py (nuevo, 2 tests) | 8/8 (test_autopilot_fact_check_gate.py) + 32/32 (test_autopilot_fact_check_gate.py + test_autopilot_editorial.py + test_autopilot_quality_gate_payload.py + test_autopilot_document_output.py) | **FIXED + VALIDATED** |

| ComfyUI roto (comfyui.py era un esqueleto con dict sin cerrar, sin nodo SaveImage y helpers inexistentes) no generaba imágenes reales | Implementación previa incompleta; el proveedor activo por defecto era solo local placeholder | `core/image_providers/comfyui.py` reescrito: workflow **SDXL Base+Refiner real** en dos pasadas (nodos 4,5,6,7,10,11,12,15,16,17,19) reconstruido a partir de los nodos especificados —el JSON exacto llegó vacío—; helpers `http_bytes`/`_env_float` en `core/image_providers/base.py`; `_generate_once` (POST /prompt → poll /history → GET /view → valida PNG → guarda) con **fallback opción A** a LocalImageProvider; script `tools/validate_comfyui.py`; `modules/image_generator/module.json` **`timeout_seconds` 180→360**; `COMFYUI_POLL_MAX_WAIT` default **300s**; **guard de presupuesto total en `generate_image`** (`IMAGE_TOTAL_TIME_BUDGET`=330s, margen 90s, `fallback_reason="time_budget_exhausted"`); **`COMFYUI_CONNECT_TIMEOUT` corto (10s)** para el POST de encolado con `fallback_reason="comfyui_unreachable"`; **flip `DEFAULT_PROVIDER="comfyui"`** — hallazgo clave: solo cambiar la constante no bastaba (el `get()` resuelve `self._default` antes que `DEFAULT_PROVIDER`), así que se movió el flag `default=True` a `ComfyUiProvider` en `_register_defaults` (local queda como fallback) | core/image_providers/comfyui.py, core/image_providers/base.py, core/image_providers/registry.py, tools/validate_comfyui.py, modules/image_generator/module.json, modules/image_generator/main.py | Script aislado contra ComfyUI real (0.33.1): imagen real (189k colores), 1:1 y 16:9 OK, ~72–95 s/imagen; fallback validado contra puerto inexistente sin excepción (~2s, razón comfyui_unreachable) y contra timeout externo; margen 240s/360s = 50%; tests existentes 20+6 PASS sin regresión + test_budget_guard_forces_local_fallback_for_remaining_images (nuevo) + 6/6 PASS en modules/image_generator/tests/ + 12 PASS (scheduler + autopilot_persist) + **resolución por default (`get(None)`) contra servidor real confirmada (Clase ComfyUiProvider, imagen real, 95.4s)** | **IMPLEMENTED + VALIDATED + ACTIVADO POR DEFECTO (DEFAULT_PROVIDER = comfyui desde 2026-08-17)** |
| Regresión en estilo por defecto de imágenes: `_build_fallback_plan` y `_normalize_images` en `image_planner` usaban `"Fotografía editorial, paleta coherente, detalle realista"` como fallback tras quitarse `or "realistic"` en `frontend/editorial.py`, dejando el estilo por defecto real de producción inconsistente con el esperado | El default hardcodeado en dos puntos de `image_planner/main.py` no se actualizó cuando `frontend/editorial.py` dejó de forzar `"realistic"` | Sustituido el default de `_build_fallback_plan` (l.204) y `_normalize_images` (l.365) por `"realistic"`, alineado con el default real de producción; test `test_no_genre_keeps_default_style` actualizado para reflejarlo | modules/image_planner/main.py, tests/test_image_planner.py | 23/23 PASS (test_image_planner.py) + grep repo-wide confirmó que el string antiguo solo persiste en `_build_prompt` (l.233, texto de guía para el LLM, no asignado a ningún campo salvo eco literal del LLM) y en `tools/gen_book30_fallback_image.py` (script auxiliar fuera de alcance) | **FIXED + VALIDATED** |
| build_payload() (image_plan) forzaba visual_style="realistic" y no pasaba genre al payload, sin FASE que lo documentara (encontrado sin confirmar en working tree, previo al commit 3aef299) | El forzado "or realistic" en frontend/editorial.py impedía que image_planner aplicara su propio fallback de estilo (ya corregido en FASE 8N.1); genre tampoco llegaba al LLM/fallback de image_plan para dar contexto de género | visual_style ahora se pasa tal cual (None si no hay dato, delegando el default a image_planner); se añade genre: book.get("genre") al payload de image_plan | frontend/editorial.py (build_payload, phase_id=="image_plan") | Cambio ya presente en working tree; sin test dedicado nuevo — cubierto indirectamente por test_image_planner.py (23/23 PASS, FASE 8N.1) | **DOCUMENTED RETROACTIVELY** (funcional, no revertido; consistente con 8N.1) |
| image_search_ratio no era seleccionable desde el front (solo columna BD sin UI) | Bloqueaba el uso real de la feature (P1 ya cerrado en 8N.2) por falta de control en index.html | Radio-group nuevo "Origen de imágenes" (0/25/50/75/100% IA vs Web, default 0.0=Solo IA) en index.html; lectura + envío en app.js::createNewBook(); persistencia clamped [0.0,1.0] en editorial.py::create_book() + columna en INSERT INTO books | frontend/index.html, frontend/app.js, frontend/editorial.py, tests/test_editorial_panel.py (test nuevo) | test_create_book_persists_image_search_ratio 1/1 PASS + regresión test_editorial_panel.py 24/24 + test_editorial_metadata.py 6/6 + node --check OK | **FIXED + VALIDATED** |
| phase['metrics'] quedaba '{}' cuando una fase con gate real (quality_gate/fact_check/research) fallaba — el desglose del módulo (ej. overall_status, book_checks) se perdía al persistir el job, dejando el FAIL sin diagnóstico posible (evidencia real: libro 32, quality_gate FAIL con metrics={}) | run_job (core/autopilot.py) solo copiaba phase['metrics'] = result.metrics en la rama de éxito (result.ok=True); la rama else (result.ok=False, antes de PHASE_RETRY/PHASE_FAIL) solo asignaba phase['error'], nunca metrics | Añadida phase['metrics'] = result.metrics or {} en la rama else, antes de la bifurcación por attempts, aplicando el mismo patrón que la rama de éxito | core/autopilot.py, tests/test_autopilot_quality_gate_payload.py | test_quality_gate_fail_persists_real_metrics (nuevo) + regresión focalizada: test_autopilot_quality_gate_payload.py 2/2 PASS, test_autopilot_fact_check_gate.py 2/2 PASS | **FIXED + VALIDATED** |
| _run_image_gen_split (ratio>0) no compensaba imágenes perdidas de search_chapter_images (contenido inválido/SVG descartado) — el capítulo quedaba con menos imágenes de las pedidas y el quality_gate fallaba en _check_images ('Imágenes por capítulo != 3'), evidencia real: libro 32, cap2/cap3 con 2 imágenes en vez de 3 | merged['results'].extend(...) solo concatenaba los resultados de search+generate sin verificar cuántos tenían status='ok' frente a num_images pedido; ningún mecanismo compensaba el déficit | Helper interno _run_img_task extraído; tras fusionar resultados se cuenta ok_count y, si ok_count < num_images, se lanza UNA tarea adicional de generate_chapter_images pidiendo el shortfall exacto (una sola ronda, sin recursión/reintento posterior) | core/autopilot.py, tests/test_autopilot_persist_chapters.py | test_image_gen_split_compensates_shortfall (versión original) + regresión test_image_gen_ratio_zero_delegates_to_run_single + test_image_gen_ratio_positive_splits_into_two_tasks, 3/3 PASS en su momento | **FIXED + VALIDATED — DOCUMENTED RETROACTIVELY (fila no registrada en su momento por un gap de proceso en esta sesión; regresión descubierta después, ver fila de seguimiento inmediatamente posterior en esta misma tabla)** |
| La ronda de compensación de _run_image_gen_split (fix anterior, fila previa) reutilizaba metadata de imagen ya existente en vez de generar contenido nuevo (skip_existing=True heredado del payload base + mismo image_plan), duplicando la misma ruta en merged['results'] y persistiéndola dos veces en chapters.images. Evidencia real: libro 36, capítulos 2 y 3 con la misma imagen insertada dos veces como figuras distintas. | comp_payload = dict(base) heredaba skip_existing=True; generate_image, al encontrar un spec ya materializado en disco, reutilizaba el resultado existente en vez de generar uno nuevo; no había deduplicación por image_path en ningún punto del merge | Helper _dedupe_by_path(results) aplicado antes de calcular ok_count/shortfall y de nuevo tras la compensación (conserva la primera ocurrencia por image_path); comp_payload['skip_existing']=False fuerza generación de material nuevo en la compensación | core/autopilot.py, tests/test_autopilot_persist_chapters.py | 6/6 PASS en test_autopilot_persist_chapters.py, incluyendo test_image_gen_split_compensates_shortfall (adaptado, reproduce el reuso real) y test_image_gen_split_dedupes_preexisting_duplicate_before_shortfall (nuevo) | **FIXED + VALIDATED** |
| Research anclaba candidatos al tema del libro (`topic`) exigiendo UNA sola keyword coincidente (`any()`) en `_has_anchor_keyword` — con topic multi-palabra como "Historia del Doom" (keywords {"historia","doom"} tras stopwords), el artículo Marvel "Doctor Doom" pasaba solo por contener "doom". Autorización concedida por el usuario para tocar modules/research/main.py (OUT_OF_SCOPE). | Causa raíz: `return any(w in haystack_words for w in topic_keywords)` (modules/research/main.py:473 previo) + `topic` por defecto = título completo del libro en frontend/editorial.py:463. | Fix mínimo: si `topic_keywords` tiene ≥2 elementos, exigir ≥2 coincidencias en el haystack tokenizado; si tiene exactamente 1, se mantiene el comportamiento previo (1 basta). No se tocó `_keyword_overlap` ni RELEVANCE_MIN_OVERLAP (0.15). | modules/research/main.py (`_has_anchor_keyword`, ~líneas 466-480), tests/test_research_sources.py (3 tests nuevos: test_doctor_doom_marvel_no_anclaje_book37, test_historia_del_doom_si_ancla_articulo_videojuego_1993, test_anchor_topic_una_sola_palabra_mantiene_comportamiento) | pytest tests/test_research_sources.py tests/test_research_multisource.py tests/test_research_curation.py → **28/28 PASS**, incluyendo el caso real book_37 (snippet real de sources.id=624 → False) y no-regresión del artículo legítimo Doom (videojuego de 1993) → True | **FIXED + VALIDATED** |
| Backstop determinista con seed=0 fija a nivel de libro (§17 #7): `_deterministic_complete()` partía siempre del mismo punto del pool compartido de hechos (`_extract_research_facts` extrae de sources con `chapter_ids` que cubren varios capítulos), así que capítulos distintos del mismo libro generaban párrafos literales idénticos (book_37.docx: mismo bloque de 5 párrafos en cap.1 pág.6 y cap.2 pág.14). Autorización concedida por el usuario para tocar modules/chapter_writer/main.py (PROTECTED). | Causa raíz: `seed = 0` fijo al inicio del bucle del backstop (main.py ~línea 1304). El caller (línea 1657) no tenía `chapter_number` como variable local (se calcula en línea 1721, después de la llamada), pero `validated["chapter_outline"]["number"]` sí es accesible dentro de `_deterministic_complete` vía su parámetro `validated`. | Fix mínimo sin tocar firmas públicas ni ninguna otra lógica (bucle de continuación, ABSOLUTE_HARD_LIMIT, presupuestos de tiempo y `_extract_research_facts` intactos): `seed = int((validated.get("chapter_outline") or {}).get("number") or 1)`. Determinismo intacto: seed sigue siendo función pura de (book_id implícito + chapter_number + hechos), ya no del orden de ejecución. Tests nuevos reutilizando el patrón determinista existente: `test_deterministic_complete_varies_output_across_chapters` (chapters 1 vs 2, mismos facts tipo book_37 con sources reales simuladas id 618/623/624-style + `chapter_ids`, comparación de string completa ≠) y `test_deterministic_complete_same_chapter_is_stable` (mismo chapter_number dos veces → idéntico). | modules/chapter_writer/main.py (`_deterministic_complete`, línea ~1304), tests/test_chapter_writer.py (2 tests nuevos + helper `_shared_book_facts`) | pytest tests/test_chapter_writer.py tests/test_chapter_writer_placeholder.py tests/test_runner_e2e_001.py → **163 passed, 0 failed** (114.73s), incluyendo los 2 tests nuevos | **FIXED + VALIDATED (primera mitad del fix)** — ⚠️ SUPERADO por regresión 2026-08-23 (ver fila siguiente y §17 #7): este fix era solo parcial |
| Regresión 2026-08-23 (book_43 caps 181/182): 3 párrafos completos verbatim idénticos entre cap1 y cap2 pese al fix anterior de seed por chapter_number | Dos capas: (1) offset de seed por capítulo (+1) insuficiente frente al incremento +1 por iteración del bucle — rangos solapados; (2) incluso separando rangos (seed*1000), el espacio combinatorio del generador (opener%8 × fact%3 × closer%5 = periodo 120) era demasiado pequeño frente al peor caso realista (~90-95 párrafos/capítulo), causando colisión por 'cumpleaños' dentro del mismo ciclo | FIX COMPLETO en dos partes: (a) seed = chapter_number*1000 (offset sin solape de rangos); (b) ampliación combinatoria del generador determinista — openers 8→12, closers 5→7, nuevo banco de 5 puentes, nueva `_elaborate_fact_pair_deterministic()` que combina PARES de hechos cuando el pool tiene ≥2 (periodo efectivo nuevo ≈420 combos, ≫2×P_max) | modules/chapter_writer/main.py (`_deterministic_complete` y generador determinista; PROTECTED, autorización puntual del usuario), tests/test_chapter_writer.py | Test de estrés en el peor caso real (minimum=3200, pool=3 hechos): cap1=29 párrafos únicos, cap2=31, intersección=0. 139 passed, 1 skipped (preexistente) en test_chapter_writer.py + test_chapter_writer_placeholder.py. Determinismo intra-capítulo intacto (test_deterministic_complete_same_chapter_is_stable PASS). Confirmación adicional con datos reales de producción (2026-08-23): ejecución offline de chapter_writer.execute() con los payloads originales de las tasks 50/51 de book_43 (modo backstop puro, peor caso sin LLM) → de 4 párrafos verbatim duplicados (baseline real) a 0. Única coincidencia residual: 1 frase de transición genérica de 13 palabras (banco de closers), aceptada como no significativa — no se abre nueva fila de backlog para esto. | **FIXED + VALIDATED (2026-08-23) — fix completo tras regresión** |
| Acabado DOCX P2 (ronda document_builder, OUT_OF_SCOPE — 6 fixes quirúrgicos A1/A2/A3/B1/B2/B3): (A1) año de copyright hardcodeado `2024` en `_add_legal`; (A2) portada mostraba header ("Título") y footer ("N \| filename.docx"); (A3) footer exponía el nombre de archivo interno al lector; (B1) enlaces markdown `[texto](url)` renderizados como texto crudo; (B2) fuentes duplicadas en el listado del capítulo; (B3) huecos en numeración "Figura N" cuando una imagen faltaba. | (A1) literal de año fijo como fallback; (A2/A3) header/footer únicos aplicados a todas las páginas y footer con `f" \| {filename}"`; (B1) `_add_formatted_text` sin soporte de `[texto](url)` (solo negrita/cursiva); (B2) causa raíz upstream en writer/editor (ver §17 #9), la cola llega ya duplicada en `edited_es`; (B3) `caption_number` se incrementaba antes de comprobar si el fichero existía. | (A1) prioridad `book.created_at.year` + fallback `datetime.now().year` (main.py:485); (A2) `different_first_page_header_footer = True` (main.py:615); (A3) footer = solo campo PAGE (main.py:621-627); (B1) hipervínculo REAL: opción 1 aplicada — nuevo helper `_add_hyperlink` con relación externa `<w:hyperlink>` vía XML de bajo nivel (mismo patrón que `_add_page_number`), subrayado + color 0563C1 estándar; regex de split ampliada sin tocar negrita/cursiva (main.py:290-343); (B2) **mitigación defensiva, NO fix**: dedupe en `_split_sources_tail` de líneas exactas normalizando espacios, conservando primera aparición y orden (causa raíz abierta en §17 #9); (B3) `_add_image_if_exists` devuelve bool y construye el caption internamente; el contador solo avanza si la imagen se insertó de verdad; contador por capítulo intacto (diseño intencional). Tests: 3 nuevos (`test_markdown_link_rendered_as_real_hyperlink`, `test_duplicate_source_lines_deduplicated`, `test_figure_numbering_no_gap_when_first_image_missing`) + test principal ampliado (A1-A3) + test de aislamiento cross-book adaptado (identidad 1:1 por path canónico, no por footer). | modules/document_builder/main.py, modules/document_builder/tests/test_document_builder.py | pytest modules/document_builder/tests/test_document_builder.py tests/test_e2e_docx_integration.py → **24 passed, 0 failed** (2.80s) | **FIXED + VALIDATED** |
| fact_checker inflaba claims_checked cuando el LLM repetía el mismo claim dentro de una única salida JSON (evidencia real: book_39 cap 173, 14 claims = 7 únicas × 2). Descartada acumulación entre reintentos de fase: phase["metrics"]/csub["metrics"] se REEMPLAZAN (=) en cada intento (core/autopilot.py:540/672/1131), sin += ni .extend() sobre issues/claims en todo el pipeline. | El bucle de normalización de issues (main.py:200-210) y el conteo claims_checked (main.py:~252) no deduplicaban por texto de claim. | Dedupe por texto de claim normalizado (lowercase+strip+espacios colapsados) en el bucle de normalización, set seen_claims, conserva primera aparición. claims_checked hereda el conteo ya deduplicado sin cambio adicional. No se tocaron _build_prompt/_parse_llm_output/_fallback_result/_heuristic_issues. Autorización puntual del usuario para tocar modules/fact_checker/main.py (OUT_OF_SCOPE). | modules/fact_checker/main.py, tests/test_fact_checker.py | test_execute_dedupes_repeated_claims (nuevo) + 14/14 PASS en tests/test_fact_checker.py | FIXED + VALIDATED (forward-only — NO corrige conteos ya persistidos en BD; book_39 cap 173 requiere re-ejecutar fact_check para reflejar el conteo correcto) |
| Títulos de capítulo rotos cuando book_planner cae al fallback determinista (LLM de planificación falla): `_fallback_plan` volcaba la idea COMPLETA del usuario (hasta 200+ caracteres) como título de cada capítulo, con "Capítulo N:" baked-in; el TOC de document_builder volvía a anteponer "Capítulo N:" encima, duplicándolo (evidencia real: book_55, 24 capítulos, título de 207 caracteres repetido en cada heading, TOC ilegible con "Capítulo 1: Capítulo 1: ..."). Adicionalmente, libros con títulos planos "Capítulo N" (sin idea baked-in, ej. books 49-54) también se duplicaban en el TOC por el mismo mecanismo, con formato más corto | `_fallback_plan` (book_planner) generaba `f"Capítulo {i}: {idea completa}"`; `_add_toc` (document_builder) anteponía "Capítulo N: " sin comprobar si el título ya lo traía, para CUALQUIER origen de título con ese prefijo | (1) Nueva helper `_short_idea_title()` en book_planner/main.py: acorta la idea a 8 palabras + "..." si trunca, título final `f"{corto} - Parte {i}"` sin prefijo baked-in, consistente con la convención del path LLM real (sin prefijo, lo añade siempre document_builder). (2) `_add_toc` (document_builder): regex defensiva `^capítulo\s+\d+\b` (case-insensitive, con o sin ":") — si el título ya empieza así, se usa tal cual sin re-prefijar; si no, se prefija como antes. (3) Reparación puntual de datos: 24 `chapters.title` de book_55 actualizados en BD de producción con la misma lógica de `_short_idea_title` (backup previo guardado); books 49-54 NO tocados en BD, su TOC deja de duplicarse solo con el fix de (2) | modules/book_planner/main.py (`_fallback_plan`, nueva `_short_idea_title`), modules/document_builder/main.py (`_add_toc`), modules/book_planner/tests/test_book_planner.py (test nuevo con idea real de 207 chars), modules/document_builder/tests/test_document_builder.py (3 tests nuevos: título con prefijo+dos puntos, título plano "Capítulo N" sin dos puntos, título sano sin el prefijo) | 74 passed + 25 passed en las dos rondas de test focalizado (document_builder). CONFIRMADO VISUALMENTE EN PRODUCCIÓN 2026-08-24: DOCX de book_55 regenerado vía pipeline real (reseteo puntual de la fase docx a PENDING, resto fases preservadas en PASS) — TOC sin duplicación ("Capítulo 1: Protocolo de 30 días... - Parte 1"), heading de cuerpo sin prefijo, 24/24 capítulos con título corto legible en vez de la idea completa | **FIXED + VALIDATED** |
| quality_gate reportaba WARNING 'Imágenes sin metadata' incluso cuando la metadata SÍ existía — afectaba tanto a image_search COMO a image_generator (no solo a image_search, como se sospechaba originalmente en §17 #12) | `_image_has_metadata` (quality_control/main.py) solo reconocía `metadata.json` simple o EXIF; ambos módulos (image_search e image_generator) persisten en convención `{image_id}.metadata.json`, nunca reconocida por el check | Añadido `candidates += list(path.parent.glob("*.metadata.json"))` a `_image_has_metadata`, sin tocar cómo persisten image_search/image_generator | modules/quality_control/main.py, modules/quality_control/tests/test_quality_control.py (test nuevo `test_image_metadata_in_image_id_metadata_json_is_recognized`) | 9/9 PASS en modules/quality_control/tests/ | **FIXED + VALIDATED** |
| Imágenes .webp no se insertaban en el DOCX (python-docx no soporta el formato nativamente) — capítulo quedaba sin esa figura, con warning no bloqueante | `_add_image_if_exists` (document_builder/main.py) pasaba la ruta .webp directamente a `doc.add_picture`, que la rechaza | Nueva helper `_prepare_image_source()`: si es .webp, conversión a PNG en memoria vía Pillow (normaliza RGB/RGBA según transparencia); `_add_image_if_exists` usa la fuente convertida; si la conversión falla, mantiene el skip-con-warning original (no rompe la fase) | modules/document_builder/main.py, modules/document_builder/tests/test_document_builder.py (test nuevo `test_webp_image_is_inserted`, webp generada en el propio test) | 26/26 PASS en modules/document_builder/tests/ | **FIXED + VALIDATED** |
| image_search podía insertar imágenes temáticamente irrelevantes (ej. book_43: imagen de comicvine.gamespot.com —portal de cómics— en un libro de historia polar) — no había filtro de relevancia temática, solo el denylist legal/marca ya existente (§16 fix #5, problema distinto) | image_search/main.py no recibía topic/título del libro en su payload; ImageSearchPayload (core/schemas.py) no tenía ese campo | (1) `topic: Optional[str]` añadido a ImageSearchPayload (retrocompatible, default None); (2) `core/autopilot.py::_run_image_gen_split` deriva `topic = job.data.topic or book.title` (mismo patrón que el fix anti-reciclaje de fuentes) y lo añade al payload; (3) image_search/main.py reutiliza `_has_anchor_keyword` (importada de research, SIN modificar research) sobre title/página fuente/img_src de cada candidato antes de descargar; topic=None/vacío no filtra nada (compatibilidad total con libros ya generados). El déficit que pueda generar el filtro ya queda cubierto por la compensación de shortfall existente (fix previo de §16, actúa por conteo, no por causa) | core/schemas.py, core/autopilot.py, modules/image_search/main.py, modules/image_search/tests/test_image_search.py (tests nuevos: `test_topic_filter_descarta_candidato_no_anclado`, `test_topic_none_no_bloquea`) | 9/9 PASS en modules/image_search/tests/ | **FIXED + VALIDATED** |
| **Fase 5 de §17 #36**: no existía forma de disparar reset_from_phase desde la UI sin usar curl/Postman — el botón "Reintentar" siempre hacía retry plano aunque el FAIL viniera de una fase anterior ya PASS (ej. image_gen bajo quality_gate, §17 #30). | Usuario debía editar BD/curl manualmente para recuperar jobs FAILED cuyo origen real era una fase distinta de quality_gate. | retryAutopilot() (frontend/app.js) ahora inspecciona state.autopilot.phases.quality_gate.metrics.*_checks, deduce el origin_phase más upstream (según AUTOPILOT_PHASES) entre los checks FAIL, y si existe llama a POST /autopilot/reset con {from_phase} tras confirm() explícito; si no hay origin_phase disponible, mantiene el retry plano de siempre (POST /autopilot/retry). Sin botón nuevo, sin selector de capítulo (decisión de alcance ya tomada). index.html/frontend_api.py/core/autopilot.py sin cambios (ya implementados en Fases 1-4). | frontend/app.js (único) | node --check frontend/app.js → OK. Sin suite JS automatizada en el proyecto; pendiente de verificación visual con un job FAILED real (ver nota). | **FIXED + VALIDATED (2026-08-28)** — verificación visual en navegador pendiente de autorización explícita del usuario para operar sobre un job real. |





### Mantenimiento / Limpieza (checkpoint 2026-08-16)

Limpieza de inventario (solo lectura → aprobada y ejecutada, Parte A) + alineación de protección:

| Categoría | Acción | Resultado |
|---|---|---|
| **Cat 1 — Huérfanos/temporales** | ~45 archivos de diagnóstico/logs/temporales borrados en la raíz y `tools/dev/`; 3 `.md` históricos archivados en `tools/dev/archive/` (`propuesta_reconciliacion.md`, `checkpoint_8d2_result.md`, `checkpoint_8d2_sources.md`) | Raíz y `tools/dev/` limpios; `e2e_001_report.json`, `e2e_book_workflow.py`, `e2e_docx_demo.py` intactos |
| **Cat 2 — Imports sin usar** | 9 imports sin uso eliminados: `core/auth.py`, `core/mcp_bridge.py`, `core/module_registry.py`, `core/workflow.py`; `tools/orchestrator.py`, `tools/dev/runner.py`, `tools/dev/autonomous.py`, `tools/dev/autopilot.py`, `tools/dev/agent_loop.py`; `frontend/editorial.py` | Collect-only limpio + 46/46 PASS en archivos afectados |
| **Cat 5 — Comentarios obsoletos** | Docstrings/comentarios de fase antigua y contador de tests hardcodeado actualizados: `core/autopilot.py` (docstring), `frontend/frontend_api.py` (54/298/703), `tools/dev/state.py` ("379 tests"→genérico) | Actualizado |
| **Cat 7 — Protección** | `OUT_OF_SCOPE_MODULES` alineado a los módulos reales en `tools/dev/config.py` y §21: +`image_planner`, `mcp_demo`, `mcp_external` | Config y documento maestro sincronizados |

**Validación (en cada paso):** `pytest tests/ -k "not e2e" -q --co` → 551/580 recolectados (29 deselected), sin roturas de import/colección; `test_autopilot_editorial.py` + `test_frontend_api.py` → 46 passed, 0 failed.

- **Cat 3 (código muerto/inaccesible)**: `core/image_providers/comfyui.py` documentado como deuda conocida (ver §19); sin acción en este checkpoint.
- **Cat 4 (patrón `or <default>`)**: verificado contra schemas — `min_sources` (`core/schemas.py:191`, `ge=1`) y `relevance` (`core/book/book_schema.py:105`, `ge=1`) ya prohíben `0` ⇒ el patrón restante es **inofensivo** y NO requiere acción.
---
# 17. PROBLEMAS ABIERTOS

> Solo problemas **realmente detectados** en código/repositorio, no supuestos.

| # | Problema | Impacto | Estado | Prioridad | Evidencia | Próximo paso |
|---|---|---|---|---|---|---|
| 1 | **`chapters.sources` no se puebla** por el chapter_writer (la columna existe, `database.py`, pero quedaba `'[]'`). | El riesgo de FAIL en QC descrito originalmente era una lectura incorrecta del código: en la ruta autómata real el QC `_check_sources` consume `chapters[].sources` desde el book dict armado por `_build_book_dict`/`_chapter_source_urls` (SourceManager), no desde la columna `chapters.sources`. El impacto real era solo inconsistencia del modelo de datos (columna sin poblar pese a existir en schema), sin impacto funcional en QC/DOCX. Corregido igualmente por consistencia. | CLOSED (ver §16). Corrección de premisa: el diagnóstico de esta sesión confirmó que el QC (_check_sources) en la ruta autómata real NUNCA dependió de chapters.sources — ya consumía las fuentes reales vía SourceManager a través de _build_book_dict/_chapter_source_urls. El problema real era solo inconsistencia del modelo de datos (columna sin poblar pese a existir en schema), no un riesgo de FAIL en QC. Corregido igualmente por consistencia. | Baja | `propuesta_reconciliacion.md` §Cambio #1; `frontend/editorial.py` | Resuelto — ver §16 (FASE 8F.2) |
| 2 | **`book_planner.execute` no devuelve `author`/`genre`/`language`** → en el autómata real, `create_book` INSERTa NULL y el QC "Metadatos completos" podría FAIL. El E2E pasa porque inyecta metadata explícita. | Antes de 8F.1: QC metadata FAIL en pipeline autómata real si book_planner no emite author/genre (el E2E pasaba porque inyecta metadata explícita). Desde 8F.1 (ver §16): QC ya no falla por esto (WARNING, no FAIL) y el DOCX ya no muestra placeholder — el único impacto restante es que el libro queda sin autor/género visible en portada/legal si el usuario no los rellena en la UI (comportamiento esperado, no bug). | CLOSED — causa raíz resuelta: `book_planner.execute()` ahora emite `language` (del payload, default "es") y `genre` (inferido keyword-based desde la idea); `author` omitido intencionalmente (no derivable honestamente). Ver §16 FASE 8J.1. | Baja | `propuesta_reconciliacion.md`; `modules/book_planner/main.py` (salida); `modules/quality_control/main.py` `_check_book` | Resuelto — ver §16 FASE 8J.1 |
| 3 | **PDF builder usa `book_{language}.pdf`** (colisión análoga a la del DOCX pre-8E.6). | Colisión de entregables si se generan varios libros PDF con el mismo idioma. | DEBT / OUT OF SCOPE | Baja (no bloqueante) | `state.json`/`PROJECT_STATUS.md` KNOWN_BAD | Corregir a `book_{book_id}_{lang}.pdf` cuando se retome PDF |
| 4 | **Bug `or 3` ignora `images_per_chapter=0`**: en `frontend/frontend_api.py:729`, `frontend/editorial.py:399/542/554` y `modules/image_generator/main.py:265`, el patrón `data.get("num_images") or 3` convierte silenciosamente 0 en 3. Descubierto durante el diagnóstico de §19 P2 (2026-08-15). | Un usuario que elige explícitamente "0 imágenes" en la UI (opción válida, radios 0/1/3/5 según §13) verá el Autopilot real generar 3 imágenes de todas formas, contradiciendo su elección. `modules/image_planner/main.py::_resolve_num_images` ya maneja 0 correctamente — el problema es que 0 nunca le llega, se transforma antes en la capa de frontend/editorial. | CLOSED (ver §16, FASE 8F.4) | Media | Diagnóstico de sesión 2026-08-15 (ver §19 P2) | Resuelto en FASE 8F.4 — ver §16 |
| 5 | **image_search no filtra por dominio/tipo de contenido** — puede insertar en el libro portadas de libros reales de editoriales (ej. McGraw-Hill, LALEO) o capturas de documentos/diapositivas de terceros (ej. Scribd, material docente universitario) con logos y nombres de autor reales visibles | Riesgo de marca registrada y atribución indebida a personas/entidades reales en un documento generado automáticamente. Evidencia real: libro 36, Figura 1 y 2 del capítulo 1 son la portada de un libro McGraw-Hill real y una diapositiva de una universidad real con nombre de docente. | FIXED + VALIDATED (2026-08-22) | Media-Alta (riesgo reputacional/legal, no solo estético) | book_36 cap1 Figura1/Figura2; modules/image_search/main.py sin denylist de dominio ni detección de portada/documento. Fix (2026-08-22): _DOMAIN_DENYLIST (11 dominios: scribd/scribdassets/slideshare/coursehero/studocu/academia.edu/issuu/docplayer/quizlet/mheducation/laleo) + _is_denylisted() comprobado contra img_src Y url/parsed_url (página fuente) ANTES de descargar; skip-and-continue sin ocupar slot. Test: test_denylist_bloquea_dominio_y_no_ocupa_slot, 7/7 PASS en modules/image_search/tests/test_image_search.py. | Diseñar denylist de dominios conocidos de editoriales/repositorios de documentos (scribdassets.com, laleo.com, etc.) y/o heurística de aspect-ratio+texto para descartar imágenes tipo portada/diapositiva, en modules/image_search/main.py (OUT_OF_SCOPE, requiere autorización) |
| 6 | Research trae fuentes temáticamente ajenas cuando el ancla de relevancia es una sola palabra genérica que coincide con el título del libro (caso real: el artículo de Wikipedia sobre "Doctor Doom", supervillano Marvel, pasó el filtro para el libro "Historia del Doom" sobre el videojuego, y contaminó los 3 capítulos con lore inventado presentado como historia real). | Contaminación narrativa grave; el libro entero mezcla ficción no relacionada con hechos reales, presentados sin distinción. Riesgo de desinformación/credibilidad del producto. | CLOSED (ver §16, fix anclaje multi-palabra `_has_anchor_keyword`). El contenido ya generado de book_37 NO se regenera automáticamente: requiere re-ejecutar research+writer del libro. | Alta | book_37: sources.id=624 (url=wikipedia.org/wiki/Doctor_Doom, relevance=127, chapter_ids=[167,168,169]); book_37.docx páginas 8,12,13,16,18,20 | Resuelto en código — ver §16. Pendiente del usuario: regenerar book_37. ⚠️ REABIERTO PARCIALMENTE 2026-08-22: book_39 (libro distinto, misma sesión) mostró de nuevo contaminación Marvel ('Latveria', 'Pantera Negra/Black Panther') detectada por fact_checker como claims sin fuente. El fix de _has_anchor_keyword (≥2 keywords para topics multi-palabra) no evitó que estas fuentes entraran en el pool de research. Pendiente de diagnóstico — ver próxima ronda. ✅ FIX ADICIONAL 2026-08-22: causa raíz real identificada — no era insuficiencia de _has_anchor_keyword sino ausencia de re-validación en la re-asociación de fuentes recicladas (SourceManager.add_source). Ver nueva fila en §16. NO revierte contaminación ya persistida en libros existentes (book_37, book_39) — limpieza de datos históricos queda pendiente, a decisión del usuario. ✅ DIAGNÓSTICO CONFIRMATORIO 2026-08-22 (research_web): lectura completa de research_web()/_curate_with_llm() confirma que el filtro overlap+_has_anchor_keyword se aplica DESPUÉS de la curación LLM y ANTES del return — no existe camino de fuga en ese archivo para búsquedas nuevas. Confirma que la única vía real de contaminación es el reciclaje de fuentes (SourceManager.add_source), ya cubierto por el fix anti-reciclaje. Cierra definitivamente la sospecha sobre research_web(). ✅ CONFIRMADO EN PRODUCCIÓN REAL 2026-08-22 con book_42 (control positivo/negativo, ver §16). ✅ CIERRE DEFINITIVO 2026-08-23: hipótesis research_web()/_curate_with_llm() descartada por trazado completo del código (filtro aplica antes del return, sin vía de escape). Único vector real confirmado: SourceManager.add_source (ver §16). Fix confirmado en producción con ejercicio directo de la rama de rechazo. |
| 7 | Backstop determinista del writer reutiliza el mismo pool de hechos y una semilla fija (seed=0) a nivel de libro en vez de por capítulo, generando párrafos idénticos repetidos entre capítulos distintos. | Duplicación literal de contenido visible al lector; rompe la percepción de calidad profesional del libro. | ⚠️→✅ REGRESIÓN DETECTADA 2026-08-23 vía revisión manual del DOCX de book_43 (capítulos 181/182): 3 párrafos completos verbatim idénticos entre cap1 y cap2. Causa raíz en DOS capas: (1) offset de seed por capítulo (+1) insuficiente frente al incremento +1 por iteración del bucle — rangos solapados; (2) incluso separando rangos (seed*1000), el espacio combinatorio del generador (opener%8 × fact%3 × closer%5 = periodo 120) era demasiado pequeño frente al peor caso realista (~90-95 párrafos/capítulo), causando colisión por 'cumpleaños' dentro del mismo ciclo. FIX COMPLETO: (a) seed = chapter_number*1000 (offset sin solape de rangos); (b) ampliación combinatoria — openers 8→12, closers 5→7, nuevo banco de 5 puentes, nueva _elaborate_fact_pair_deterministic() que combina PARES de hechos cuando el pool tiene ≥2 (periodo efectivo nuevo ≈420 combos, ≫2×P_max). Test de estrés en el peor caso real (minimum=3200, pool=3 hechos): cap1=29 párrafos únicos, cap2=31, intersección=0. 139 passed, 1 skipped (preexistente) en test_chapter_writer.py + test_chapter_writer_placeholder.py. Determinismo intra-capítulo verificado intacto (test_deterministic_complete_same_chapter_is_stable PASS). Estado final: FIXED + VALIDATED (2026-08-23) — fix completo tras regresión, ver §16. | Alta | book_37.docx: mismo bloque de 5 párrafos repetido en cap.1 (pág.6) y cap.2 (pág.14); modules/chapter_writer/main.py: _extract_research_facts (usa research/sources compartidos del libro) + _deterministic_complete (seed=0 fijo, línea ~1279) | Resuelto — ver §16 (autorización concedida por el usuario para tocar main.py PROTECTED) |
| 8 | Ningún módulo detecta mensajes de rechazo/negativa del LLM (ej. "Lo siento, pero no puedo ayudar con eso.") como placeholder; se insertan íntegros en el documento final. | Texto claramente roto y no profesional visible directamente al lector. | CLOSED (ver §16, fix REFUSAL_PATTERNS). Cubierto en chapter_writer (rechazo de continuación, mismo camino que rejected_duplicate) y editor (fallback determinista). Pendiente en research/main.py (fuera de alcance). ✅ CIERRE DEFINITIVO 2026-08-23: diagnóstico de solo lectura confirmó que research/main.py no tiene camino de propagación — _curate_with_llm() exige respuesta JSON estricta, descarta explícitamente URLs inventadas por el LLM, y cualquier salida no-JSON (incluido un refusal) cae a fallback determinista con WARNING. El riesgo original de #8 (texto de refusal insertado en el DOCX) solo existía en writer/editor, ya FIXED + VALIDATED. Riesgo en research: MITIGADO ESTRUCTURALMENTE, no requiere cambio de código. Sin autorización de OUT_OF_SCOPE solicitada. | Alta | book_37.docx página 11, capítulo 2 ("Doom II: El Despertar"); PLACEHOLDER_PATTERNS en modules/chapter_writer/main.py:27-37 no cubre frases de rechazo; mismo patrón ausente en modules/editor/main.py y modules/research/main.py (grep sin coincidencias) | Ninguno — cerrado sin cambio de código, ver diagnóstico 2026-08-23. |
| 9 | ⚠️ CAUSA RAÍZ CONFIRMADA (diagnóstico 2026-08-23, solo lectura) — COMÚN a #9/#13/#14: document_builder/_add_chapter (líneas 581-612) NO construye la sección de fuentes desde chapters.sources/SourceManager — parsea literalmente el heading '## Fuentes utilizadas' que el propio LLM del writer escribe dentro de edited_es/draft_es (_split_sources_tail, líneas 414-446). El prompt del writer (chapter_writer/main.py:1086) ordena explícitamente generar esa sección, en contradicción con su propia línea 1108 ('si el pipeline ya añade la sección, no generes una segunda') — contradicción que asume un fallback en document_builder que NUNCA existió. Sin validación de las citas generadas contra 'Allowed sources' del payload. Fix candidato pendiente de autorización: ver decisión del arquitecto. — Cola de "Fuentes utilizadas" se DUPLICA en el texto del capítulo (`edited_es`) antes de llegar a document_builder — causa raíz no identificada (¿writer? ¿editor?). Evidencia BD: `chapters.sources` de book_37 (ids 167/168/169) tiene 7 URLs únicas SIN duplicados, pero `edited_es` del capítulo 3 (id=169) contiene la misma lista de 5 fuentes `(web_searxng)` repetida 2 veces en la cola. | Listado de fuentes repetido visible al lector (mitigado visualmente por el dedupe del renderer, ver §16 B2, pero la causa real sigue sin investigar). | CLOSED (ver §16, fix document_builder 2026-08-23) | Baja (mitigado visualmente por B2 en document_builder; la causa raíz sigue sin diagnóstico) | book_37 capítulo 3 (chapters.id=169), edited_es cola de fuentes duplicada 2 veces; caps. 1-2 (ids 167/168) NO duplicados pero con markdown crudo `[Título](url)` (render resuelto por B1). document_builder nunca lee `chapters.sources` (core/book/book_schema.py:67) | Diagnóstico de solo lectura COMPLETADO 2026-08-23 (ver nota común de causa raíz). Próximo paso unificado con #13/#14: decisión del arquitecto sobre el fix (construir la cola determinísticamente desde fuentes reales del payload y/o eliminar la instrucción del prompt) — requiere autorización para tocar módulos PROTECTED/OUT_OF_SCOPE. NO re-analizar por separado. |
| 10 | Hallazgo de fact_checker duplicando el set de claims evaluadas dentro de un mismo capítulo (book_39 cap 173: 14 claims = 7 únicas × 2; mencionado en NEXT_RECOMMENDED_ACTION previo del §23). | Inflado del contador claims_checked mostrado al usuario; sin impacto en gates (quality_gate no depende del número de claims). | CLOSED — causa raíz identificada y corregida: el bucle de normalización de issues no deduplicaba por texto de claim; fix de dedupe normalizado aplicado en modules/fact_checker/main.py, ver fila nueva de §16. ⚠️ Los datos históricos de book_39 (y cualquier otro libro con este síntoma) NO se corrigen automáticamente — forward-only; re-ejecutar fact_check sobre esos capítulos si se quiere el conteo correcto (pendiente de decisión del usuario, ver §23). | CLOSED (ver §16) | Diagnóstico de sesión 2026-08-22: tasks 8/9 de fact_check_chapter en BD con claims casi idénticos; descartada acumulación entre reintentos (phase["metrics"]/csub["metrics"] se reemplazan, core/autopilot.py:540/672/1131); test_execute_dedupes_repeated_claims (nuevo) + 14/14 PASS | Cerrado a nivel de código; limpieza/re-ejecución de datos históricos pendiente de decisión del usuario |
| 11 | image_search puede insertar imágenes de dominios temáticamente irrelevantes (no problemáticos por marca/atribución, sino por tema) — evidencia real: book_43 capítulo 1, imagen de comicvine.gamespot.com (portal de cómics) en un libro de historia polar. Distinto del problema ya cerrado en §17 #5 (ese era sobre marca registrada/atribución indebida). | Estético/calidad, no legal. Baja prioridad. | CLOSED (ver §16, fix image_search topic filter 2026-08-24) | Baja | book_43 cap1, img_01_web.jpg, source_url dominio comicvine.gamespot.com, no cubierto por _DOMAIN_DENYLIST (que apunta a editoriales/repositorios, no a relevancia temática); diagnóstico solo-lectura 2026-08-23: confirmado que NO existe filtro de relevancia temática en image_search (solo _DOMAIN_DENYLIST legal, distinto problema); _has_anchor_keyword (research/main.py) es reutilizable por import sin modificar research (mismo patrón que autopilot.py), pero image_search no recibe topic/título del libro en su payload hoy — requeriría diseño previo. Muestreo de 35 metadata.json reales: 7 imágenes de i.ytimg.com (thumbnails YouTube, dominio genérico sospechoso) + varios casos de relevancia dudosa (blog, prensa regional, webinar gubernamental). | Evaluar heurística de relevancia temática adicional (ej. comparar dominio/título del resultado contra topic del libro, similar a _has_anchor_keyword de research) o ampliar denylist con dominios de entretenimiento/cómics — decisión pendiente, sin autorización de implementación todavía. |
| 12 | quality_gate reporta WARNING 'Imágenes sin metadata' incluso cuando las imágenes de image_search SÍ tienen su *.metadata.json persistido — posible falso positivo por diferencia de convención de rutas entre image_generator e image_search. | Cosmético (WARNING no bloqueante), pero puede confundir en revisiones futuras de QC. | CLOSED (ver §16, fix quality_control metadata glob 2026-08-24) — alcance ampliado en el fix: afectaba también a image_generator, no solo image_search. | Baja | book_43, quality_gate overall=WARNING con este mensaje pese a documento y DOCX generados correctamente. | Diagnóstico de solo lectura en quality_control/main.py (check de metadata de imágenes) comparando la ruta/campo que espera contra la que realmente escribe image_search/main.py. |
| 13 | ⚠️ CAUSA RAÍZ CONFIRMADA — COMÚN a #9/#13/#14 (ver nota completa en fila #9): document_builder NO construye la sección desde chapters.sources; parsea la cola que el LLM del writer escribe en edited_es/draft_es (instrucción explícita del prompt, main.py:1086, contradiciendo su línea 1108), sin validar contra 'Allowed sources' del payload. Fix pendiente de autorización. — Writer LLM fabrica citas bibliográficas APA completas inexistentes (autores/editoriales/ISBN inventados) al generar la cola "## Fuentes" del capítulo, en vez de usar las fuentes reales del payload. | Desinformación/credibilidad: citas falsas presentadas como referencias reales al lector. Misma clase de riesgo que §17 #6 pero originado en writer (no en research). | PARTIALLY FIXED — mitigado a nivel de DOCX (ver §16): el lector ya nunca ve citas fabricadas en el documento final, porque document_builder ya no renderiza texto libre del LLM para la sección de fuentes. CAUSA DE FONDO SIGUE ABIERTA: el writer continúa fabricando citas APA falsas dentro de draft_es/edited_es en BD (dato sucio persistente, sin impacto en output). Requeriría tocar chapter_writer/main.py (PROTECTED, prompt línea 1086 vs contradicción 1108) — no autorizado en esta sesión, prioridad baja por dejar de ser bloqueante para el producto. | Alta | book_47 cap2 (chapter_id=192): bloque "## Fuentes" con 3 citas APA falsas (Smith & Johnson 2019, Brown 2020, White & Black 2018 — ninguna existe en sources de BD, que sí traía 8 fuentes reales Wikipedia/abacus/amazon en el payload de write_chapter_es) | Ninguno urgente. Si se retoma: corregir contradicción de prompt 1086/1108 en chapter_writer (requiere autorización explícita, PROTECTED). |
| 14 | ⚠️ CAUSA RAÍZ CONFIRMADA — COMÚN a #9/#13/#14 (ver nota completa en fila #9): al no existir fallback en document_builder, si el LLM omite o trunca la cola instruida por el prompt (main.py:1086), el capítulo sale sin fuentes o con cola off-topic. Fix pendiente de autorización. — Writer omite la cola de fuentes en algunos capítulos y en otros genera una cola truncada/off-topic, de forma inconsistente, pese a recibir siempre las fuentes reales en el payload. | Inconsistencia de calidad entre capítulos del mismo libro; cola off-topic desorienta al lector. | CLOSED (ver §16, fix document_builder 2026-08-23) | Media | book_46: ch189 (cap2) sin cola alguna; ch190 (cap3) cola con 1 sola fuente "El arte carolingio (web_wikipedia)", off-topic para un libro sobre confianza; book_47 ch191/ch193 también sin cola. Distinto de #9 (duplicación, no omisión/fabricación) | Próximo paso unificado con #9/#13: misma decisión pendiente del arquitecto sobre la causa raíz común — NO re-analizar por separado; requiere autorización (PROTECTED/OUT_OF_SCOPE). |
| 15 | fact_checker no detecta citas bibliográficas fabricadas: las fabricaciones del writer (#13) pasan sin ningún warning. | El gate de fact_check da falsa sensación de validación; las citas falsas llegan al DOCX sin señal alguna. | DOWNGRADED — DIAGNOSED, NO ACTION NEEDED (2026-08-26) | Baja (impacto en lector nulo desde fix document_builder 2026-08-23; riesgo residual solo dato sucio en BD) | Diagnóstico solo lectura 2026-08-26: (1) las citas fabricadas SÍ viajan dentro de chapter_text al fact_checker (frontend/editorial.py build_payload fase fact_check pasa edited/draft crudo sin recortar), pero el prompt de fact_checker (_build_prompt) extrae "afirmaciones factuales del cuerpo" y no clasifica una bibliografía como claim — por eso no se generan issues (evidencia original ch192: claims_checked=1, INFO no relacionado). (2) _has_fabrication_signature() SÍ matchearía una cita APA falsa ("Smith & Johnson (2019)...") si llegara como issue sin soporte, pero solo actúa sobre issues ya generados por el LLM (nunca se genera ninguno para bibliografía); haría falta extracción determinista del formato bibliográfico validada contra sources permitidas, no solo el patrón existente. (3) Prevalencia: único caso documentado era book_47/ch192, que YA NO EXISTE en la BD de producción (libros antiguos borrados; hoy solo books 65/66). Barrido completo de la BD actual → 0 casos de citas APA fabricadas. (4) Impacto lector: nulo — document_builder descarta la cola del LLM (_split_sources_tail) y reconstruye la sección de fuentes determinísticamente desde chapters.sources | Decisión: NO se autoriza tocar fact_checker ni chapter_writer para esto ahora; deuda de datos (no de producto) registrada en §19 P3. Se revisará si reaparece evidencia en producción con datos reales |
| 16 | ⚠️ CORRECCIÓN DE PREMISA (2026-08-23, diagnóstico solo-lectura): los DOCX book_46/book_47 SÍ existen hoy físicamente en output/docx/. Reportado originalmente como "DOCX fantasma" (PASS sin fichero); verificación posterior desmiente la ausencia. | Si la premisa se hubiera mantenido: docx_status=PASS dejaría de ser evidencia fiable. Al existir ambos ficheros, el riesgo actual se reduce a la falta de verificación post-save en document_builder (ver diagnóstico). | CLOSED (ver §16, fix hardening doc.save() 2026-08-26) | Alta→Baja (tras corrección) | book_46_es.docx: CreationTime 23/08/2026 21:42:24 local, 759.619 bytes; book_47_es.docx: CreationTime 22:28:32-33 local, 12.866.341 bytes — coinciden exactamente con tasks BD 132 (created 2026-08-23 19:42:24 UTC) y 158 (20:28:32-33 UTC), offset +2h CEST. Último reinicio de servidor ~16:08 (server_err.log), anterior a ambas generaciones. Sin script de limpieza que toque output/docx/ encontrado en repo | Diagnóstico completo en sesión 2026-08-23: build_book_docx NO envuelve doc.save() en try/except ni verifica os.path.exists/tamaño tras guardar (main.py:699-707) — hardening recomendado, requiere autorización (OUT_OF_SCOPE) |
| 17 | Backstop determinista genera plantillas casi-verbatim repetidas DENTRO de un mismo capítulo (distinto de #7, que era duplicación exacta ENTRE capítulos, ya cerrado) — mismo patrón de frase aplicado a sujetos distintos en secciones consecutivas, con término musical incorrecto ("sonata" aplicado a bandas de rock) reutilizado sin variación semántica | Cosmético/calidad de redacción, no funcional | OPEN | Baja | book_48 cap2 (Queen/Led Zeppelin/Los Hermanos Rosales), mismo patrón de frase "creando una sonata única que cautivó a fans de todo el mundo" 3 veces | Sin acción por ahora — requeriría tocar chapter_writer/main.py (PROTECTED); no bloqueante |
| 18 | Imágenes en formato .webp no se insertan en el DOCX (warning "No se pudo insertar la imagen" durante build_book_docx, ~5 capítulos afectados en book_55) — python-docx no soporta webp nativamente | No bloqueante (la fase termina PASS, el capítulo queda sin esa imagen concreta) | CLOSED (ver §16, fix document_builder webp→PNG 2026-08-24) | Baja | book_55, warnings durante regeneración 2026-08-24 | Evaluar conversión webp→png/jpg antes de insertar en document_builder, o filtrar el formato en image_search aguas arriba — sin autorizar todavía |
| 19 | 4 tests en `tests/test_chapter_writer.py` esperan que el prompt del writer instruya generar la sección de fuentes (comportamiento anterior al fix de §16/§17 #9); el código actual ya NO contiene esa instrucción (`chapter_writer/main.py` L1085 ES / L1032 EN dicen explícitamente lo contrario) — tests desactualizados respecto a un cambio no documentado en su momento. Un 4º test contiene además un bloque de aserciones huérfano (L307-311, `NameError` garantizado, resto copiado de `test_fallback_chapter_shape`). | Bloquea declarar la suite 100% verde; sin impacto en producción (chapter_writer/main.py es PROTECTED y su comportamiento real no cambió). | OPEN | Media | Checkpoint suite completa 2026-08-24: 673 passed, 4 failed (exclusivamente estos 4), 1 skipped (262.31s); confirmado por lectura directa que la instrucción de fuentes ya no existe en el prompt (PROTECTED, sin tocar) | Decidir: actualizar los 4 tests para reflejar el prompt actual, o documentar el cambio histórico si fue intencional; eliminar el bloque de aserciones huérfano (L307-311) — requiere autorización (tests/ es PROTECTED) |
| 20 | **El pipeline puede fabricar hechos históricos falsos con apariencia factual** (fechas, nombres propios, cifras de víctimas) sobre eventos reales y sensibles, sin ningún respaldo en las fuentes provistas, y entregarlos en el DOCX final sin bloqueo. Evidencia: book_59 "Historia Completa del Genocidio en Palestina" cap.2 — el outline (book_planner, task 462) exigió como sección obligatoria "campamentos de concentración en Palestina" sin base en ninguna fuente; el writer (`execution_mode=real`, LLM, NO backstop determinista) inventó detalles concretos (Adolf Eichmann, campos en Majdal Shams/Nahariyya/Jaffa/Safed operativos 1942-1948, 50.000-100.000 víctimas) sin research (`research=null`) y con fuentes reales de solo 148-1.373 caracteres que no mencionan nada de esto (verificado por regex sobre el campo `content`). fact_checker (task 472) detectó las claims como "sin soporte en fuentes" pero las clasificó WARNING, nunca ERROR; `quality_gate=PASS` pese a `status=FAIL` — el contenido llegó al DOCX final sin bloqueo. | Contenido histórico fabricado presentado como real en el entregable final, sobre un tema extremadamente sensible; riesgo reputacional y de desinformación grave. El sistema completo (planner→writer→fact_checker→gate) carecía de cualquier barrera contra fabricación factual con especificidad verificable. | **FIXED + VALIDATED (2026-08-24)** — fix en 3 capas: (1) fact_checker (`modules/fact_checker/main.py`): `_has_fabrication_signature()` (fecha+cifra+nombre propio, o bigrama de nombre propio compuesto tipo "Adolf Eichmann"/"Majdal Shams") + `_is_unsupported_issue()` + `_escalate_fabrication_issue()` → claims con especificidad factual verificable sin soporte escalan a ERROR; `quality_gate=FAIL` si hay algún ERROR (antes solo fallaba por research_required/texto insuficiente). (2) chapter_writer (`main.py`, PROTECTED): instrucción anti-invención en `_build_prompt` (ES/EN) junto a las reglas existentes — generalizar en vez de fabricar especificidad cuando las fuentes no cubren un tema del outline. (3) book_planner (`main.py`, OUT_OF_SCOPE) + `core/schemas.py` (`BookPlanPayload.sources`, opcional, retrocompatible) + `frontend/editorial.py` (build_payload de la fase outline incluye ahora fuentes reales resumidas — antes nunca llegaban al planner pese a estar disponibles tras research) → regla de anclaje a fuentes en el prompt del outline; sin sources, prompt idéntico al anterior. Validación: 12 tests nuevos (3 fact_checker + 1 chapter_writer + 3 book_planner + 2 editorial/integración, +3 derivados), incluida la reproducción exacta del caso real book_59 en las 3 capas; checkpoint suite completa **685 passed, 4 failed (idénticos y preexistentes §17 #19), 1 skipped, 131.49s — sin regresión**. Limitaciones reconocidas: capa (2) es mitigación probabilística de prompt, no garantía (la barrera dura es la capa 1); capa (3) depende de que research haya producido fuentes reales — libros sin research_required o con fuentes vacías mantienen el comportamiento previo. book_59 se conserva INTACTO como evidencia histórica — no se regenera ni se publica. Detalle: §16 y §24. | **CRÍTICA** (por encima del resto; máximo previo en tabla: Media) | book_59 cap.2 (`chapters.id=371`, tasks 462/465/466/472), diagnóstico de sesión 2026-08-24. book_59 se conserva INTACTO como evidencia — no se regenera ni se publica. | Causa raíz en 3 capas: (1) book_planner genera outlines con secciones temáticas no ancladas a fuentes/idea reales; (2) chapter_writer sin research real inventa especificidad factual (fechas/nombres/cifras) en vez de generalizar; (3) fact_checker no tiene categoría de severidad para "afirmación con especificidad verificable sin ningún soporte", y su gate no bloquea aunque status=FAIL. Archivos implicados: `modules/book_planner/main.py` (OUT_OF_SCOPE), `modules/chapter_writer/main.py` (PROTECTED), `modules/fact_checker/main.py` (OUT_OF_SCOPE), `core/autopilot.py` (gate). Diseño de fix pendiente de autorización explícita del usuario para las 3 capas (ver discusión de sesión 2026-08-24); candidato propuesto: nueva severidad ERROR en fact_checker para claims con fecha+nombre+cifra sin soporte, gate bloqueante para esa categoría, e instrucción anti-invención en el prompt del writer. |
| 21 | **Plan editorial monolingüe en libros bilingües ("es,en")**: el planner generaba título de libro, descripción, títulos de capítulo y headings de sección UNA sola vez y solo en español (payload planner sin idioma por edición, prompt íntegramente español); ese plan ES se compartía con la edición EN sin traducción — writer EN lo recibía tal cual (y `_canonicalize_headings` re-imponía los headings canónicos españoles), document_builder interpolaba `chapters.title` español en TOC/headings/captions "Chapter N: ..." e image_planner/image_generator alimentaban ComfyUI con el título/texto español. Evidencia: book_62_edición_EN (PDF conservado como evidencia): 20/20 títulos de capítulo y subcabeceras "Introducción"/"Desarrollo" en español, "Conclusión" mezclada 12/8, prosa EN correcta, texto gibberish hispano-imitado en imágenes ComfyUI. | La edición EN de libros bilingües salía con estructura (TOC, títulos, cabeceras, captions) en español pese a prosa inglesa; prompts de imagen con texto español → gibberish visual. No bloqueante para el pipeline, pero defecto editorial grave. | **FIXED + VALIDATED (2026-08-25)** — Opción A (plan bilingüe completo): migración DB (`books.title_en/description_en`, `chapters.title_en/outline_en`, patrón idempotente), planner genera traducción EN con UNA llamada LLM extra tras el plan ES (validación all-or-nothing: alineación de conteos índice a índice, cadenas no vacías, rechazo si es byte-idéntico al ES; fallback determinista SIN LLM extra pero mapea outline_en canónico Introducción→Introduction etc.), consumo en editorial (`build_payload` writer EN y `_build_book_dict` seleccionan _en cuando bilingüe+EN+no NULL, fallback ES explícito con log INFO). document_builder/chapter_writer/image_planner/image_generator SIN CAMBIOS (consumen payload). Ver §16 y §24. Enmienda 2026-08-25: cerrada asimetría donde el camino de ÉXITO del plan principal perdía outline_en por completo si la traducción fallaba validación — ahora aplica el mismo mapeo determinista _deterministic_outline_en que _fallback_plan en ese caso. 66/66 tests (antes 65). | Alta | Diagnóstico 2026-08-25 sobre book_62_en.pdf; 65/65 tests (test_book_planner.py + test_editorial_bilingual_plan.py); humo offline real con payload exacto book_62 | Los 6 libros bilingües existentes (56-60, 62) quedan con campos _en NULL por diseño (no se retro-traducen); regeneración de book_62 como paso manual posterior pendiente de decisión |
| 22 | **Generación del plan editorial principal (book_planner, LLM real) falla por truncamiento/JSON inválido cuando se piden ~20 capítulos**, cayendo sistemáticamente a `_fallback_plan`. Evidencia: book_62 (log original de producción: "Expecting value: line 30 column 5") y humo offline de la sesión de fix §17 #21 (2026-08-25: mismo error reproducido con payload idéntico — idea literal, language="es,en", 20 capítulos, Ollama saludable). | Medio — no bloquea el job (el fallback determinista siempre produce un libro válido), pero degrada calidad en dos frentes: (1) títulos de capítulo genéricos vía `_short_idea_title` en vez de temáticamente distintos (deuda ya conocida, §19 P3 "títulos genéricos");  (2) tras el fix de §17 #21 + enmienda de hoy, en este camino `title_en`/`description_en` quedan None por diseño → libros bilingües con muchos capítulos seguirán mostrando título de capítulo/portada en español pese al fix; PERO `outline_en` ya NO se pierde (enmienda 2026-08-25 aplica `_deterministic_outline_en` como red de seguridad también en el camino de éxito con traducción fallida), así las cabeceras de sección quedan traducidas (Introduction/Development/Conclusion) aunque `title_en`/`description_en` no. | CLOSED (ver §16, fix max_tokens dinámico 2026-08-25) | Media | book_62 (log producción); humo offline sesión 2026-08-25 (script ya borrado, resultado documentado en §16/§24 del fix §17 #21) | Diagnóstico de solo lectura de la causa exacta del truncamiento en `modules/book_planner/main.py::execute()` (max_tokens de la llamada principal, longitud de prompt para 20 capítulos) — pendiente de priorización |
| 23 | **Backstep determinista de chapter_writer inserta textos en español crudos en prosa en inglés** (`_extract_research_facts` emplea `src.text_en or src.text_es` sin chequeo de idioma → snippets de fuentes ES se insertan verbatim en párrafos EN; `_deterministic_section_paragraphs` usa la plantilla backstep `«{heading}», develops the following axis: {objective}..` con `heading`/`objective` del outline EN español sin traducir). Evidencia: book_63_en.pdf (20 capítulos; diagnóstico offline 2026-08-25: 15/20 capítulos con señales de español [caps 1,2,5,7,8,9,10,12,13,15,16,17,18,19,20], 5 limpios [3,4,6,11,14]; patrones repetidos "Presentar el capítulo, el tema y su objetivo.." en 8/20 caps + snippets "Alimententos que te ayudan a tener una vida longiva" de fuentes AARP en comillas dentro de prosa EN). | Alto — texto en español (citas, snippets de fuentes, headings de backstep) mezclado en prosa EN visiblemente fuera de lugar en libro profesional; degrada percepción editorial. No bloquea el job pero defecto grave de calidad. | PARTIALLY FIXED (2026-08-25) — ver fix parcial en §16. Diagnóstico causa raíz original: `modules/chapter_writer/main.py:1357` `_extract_research_facts` (compartida, sin chequeo de idioma; el detalle `text_en or text_es` del diagnóstico offline ya no existe en el código actual: hoy las fuentes se leen por content/title genéricos) + fallbacks cruzados ES→EN en `frontend/editorial.py::build_phase_payload`. Huecos (i) objective ES forzado y (ii) fallback cruzado de sources/research ES→EN CERRADOS (ver §16, 2026-08-25). Hueco residual sin cerrar: libros bilingües donde outline_en/description_en quedan NULL por diseño (planner bilingüe no ejecutado o traducción fallida) siguen cayendo por fallback explícito al outline/título ES en la pasada writer EN (editorial.py:669-682), reproduciendo headings/objectives españoles en prosa EN. | Alto | CONFIRMADO CON DATOS REALES 2026-08-25 (book_62, es/en): 15/20 capítulos afectados. Dos patrones distintos: (A) plantilla `«Introducción», develops the following axis: Presentar el capítulo, el tema y su objetivo con la información de las fuentes AARP en español.` en 8 capítulos (108-440 ocurrencias); (B) fragmentos completos de prosa española insertados en párrafos EN, en 7 capítulos (2-150 ocurrencias). Script y book_62 intactos, sin cambios de código. | book_63_en.pdf (conservado como evidencia); book_62 (conservado como evidencia, intacto); diagnóstico de solo lectura 2026-08-25 (script temporal borrado); re-diagnóstico contra código actual 2026-08-25: patrón (A) probablemente stale para libros post-fix §17 #21 (headings ya provienen de chapters.outline_en); huecos de objective ES y fallback de fuentes ES→EN cerrados hoy (ver §16) | Cierre total requiere: (1) decidir política para libros históricos sin campos _en (¿bloquear pasada EN, WARNING, o traducción determinista?); (2) evaluar chequeo de idioma en _extract_research_facts (modules/chapter_writer/main.py PROTECTED, requeriría autorización) |
| 24 | **image_search inserta imágenes con texto REAL en español y marca de tercero visible en libros EN** (texto legible del snippet fuente o dominio de marca insertado como contenido visual por ComfyUI/SDXL, distinto de §17 #5 denylist y distinto del gibberish de IA de book_62). Evidencia: book_63_en.pdf — figura con marca de agua "MUNDOENTRENAMIENTO.COM" + texto español completo, y página de revista con "FÍSICA" en portada insertada como contenido visual. | Medio-Alto — texto/marca en español visible en imágenes que acompañan prosa EN; riesgo de atribución/marca fuera de contexto; similar categoría calidad/riesgo a §17 #5 (arreglado parcial). | CLOSED (ver §16, fix image_search+image_planner+editorial idioma 2026-08-25). Diagnóstico original: no existía filtro de "texto visible en idioma/marca inadecuado en imágenes insertadas"; `_DOMAIN_DENYLIST` (§17 #5) no cubre casos de texto superpuesto o snippet visual. Causa raíz confirmada contra código actual 2026-08-25 y cerrada por triple vía (título EN en la query, filtro language de SearXNG, prompt SDXL en inglés con regla reforzada sin texto/marcas) | Medio-Alto | book_63_en.pdf (conservado como evidencia) | Pendiente priorización |
| 25 | **fact_checker LLM asigna severity=ERROR** (bloqueante, dispara quality_gate=FAIL) por juicio subjetivo de exactitud en claims SIN firma de fabricación estructural ni marcador de falta de soporte (fix §17 #20 no las cubre) — veredicto inestable entre reintentos del mismo claim | Jobs fallidos (FAILED) por contenido factualmente defendible pero no anclado en fuentes del capítulo; agotaba reintentos de fase. Confirmado en 2 libros el mismo día (book_64, book_65) | **FIXED + VALIDATED (2026-08-25)** — ver fix en §16 | Media | book_65 cap.431 claim "El café Liberica es una variedad única..." (ERROR sin source_url, veredicto subjetivo, gate=FAIL); book_64 claims sobre cafés históricos de Madrid con el mismo patrón | Ninguno — ver fix en §16 |

| 26 | **Placeholder de LocalImageProvider sin metadata se cuela en chapters.images** — un fallback local (PNG de color sólido generado cuando ComfyUI no responde) entró en chapters.images del libro 65 sin tener metadata (*.metadata.json) en el directorio del libro — posible residuo de un intento anterior mezclado en el merge de _run_image_gen_split; la dedup por image_path (_dedupe_by_path) no filtra placeholders sin metadata asociada | Bajo — una figura de color sólido aparece en el DOCX como imagen rota/placeholder | CLOSED (ver §16, fix persist_chapter_images filtro huérfanas locales 2026-08-25) | Baja | libro 65, data/images/local/934705850_a938610a44c0.png, 1024×576 RGBA de un solo color sólido (195,64,108), sin metadata.json en data/images/books/65/chapters/*/images/ | Diagnóstico pendiente de por qué un resultado con provider=local (placeholder) se persiste en chapters.images cuando el proveedor pedido era comfyui/web; evaluar si _dedupe_by_path o el merge de resultados debería descartar entradas sin metadata en disco |
| 27 | **research trae snippets de redes sociales (TikTok/Instagram) con metadata de engagement (fecha+likes+comentarios) como fuente citable, y el writer LLM los copia verbatim en la prosa del capítulo** — el fact_checker bloquea correctamente el resultado (patrón fecha+nombre+cifra → ERROR estructural), pero la causa raíz está en research/writer, no en fact_checker. | Bloquea el job (quality_gate=FAIL) en libros donde SearXNG devuelve resultados de redes sociales sobre el tema; contenido de baja calidad (posts virales, no editorial) filtrándose como "fuente" válida al pipeline. | CLOSED (ver §16, fix denylist redes sociales 2026-08-26) | Media | book_66 (tareas sueltas, no ejecutado vía Autopilot/workflow real), chapter_id=458, task_id=987: payload.sources[8] (tiktok.com/@teban_cometacos) y [9] (instagram.com/reel/...) devueltos por SearXNG con source_type=web_searxng; su content (snippet "1181 me gusta,30 comentarios..." / fechas "25 ene 2026" etc.) aparece copiado verbatim en payload.chapter_text de la misma task y en chapters.draft_en (id=458, byte-a-byte confirmado en los mismos índices); las 3 claims con fecha del fact_checker (issues de task 987) citan exactamente ese texto — cadena causal confirmada dato a dato (research→writer→fact_checker) en sesión de diagnóstico 2026-08-26. | Diseño pendiente de autorización: opción más quirúrgica es un denylist/deprioritización de dominios de redes sociales (tiktok.com, instagram.com, facebook.com, twitter.com/x.com — mismo patrón que _DOMAIN_DENYLIST ya usado en image_search, §17 #5) en la etapa de curación de candidatos de modules/research/main.py (OUT_OF_SCOPE, requiere autorización), para que este tipo de contenido nunca llegue como "fuente" al writer. Alternativa no excluyente: instrucción en el prompt del writer para no copiar snippets de fuente verbatim (más débil, mismo patrón probabilístico que la mitigación de §17 #20). |
| 28 | **Capabilities únicas de imágenes sin idioma nativo** — search/generate de imágenes compartían una única query+anclaje (reutilizando _has_anchor_keyword de research, monolingüe ES y pensado para texto rico): en libros bilingües/EN se descartaban en masa candidatos web claramente on-topic porque keywords ES nunca matcheaban slugs/títulos EN (haystack ≈ URLs de imagen/página), sin traducción ni normalización de acentos («café» ≠ «cafe»). Distinto de §17 #24 (param language de SearXNG / title_en del generador IA) y de §17 #11 (filtro temático original, umbral correcto). Evidencia real: book_67 «Todo sobre el café…»: unsplash «a-cup-of-coffee-sitting-on-top-of-a-white-counter» y pexels «anonymous-barista-pouring-water-into-filter» logueados como «resultado descartado por no anclarse al tema». | Pérdida masiva de imágenes web relevantes en libros no-ES (cada libro EN pierde prácticamente todo el pool web); calidad visual del DOCX degradada; coste recaído en generación IA/local como sustituto. | CLOSED (ver §16, fix capabilities ES/EN nativas images + routing autopilot/editorial 2026-08-26) | Media | book_67 (diagnóstico solo lectura 2026-08-26): haystack real del filtro = título de página + URL fuente + URL de imagen (casi todo slug EN); keywords tras stopwords ES = [todo, café, descubrimientos, tipos, cafe, mundo] → 0 hits vs umbral hits>=2; hipótesis de mismatch de idioma CONFIRMADA código en mano (_has_anchor_keyword research/main.py:543-578 + invocación image_search/main.py:337-359) | Cerrado en 2 pasos autorizados (módulos + schemas/autopilot/editorial, ver §16). Notas residuales: (a) paso opcional 3 — exponer/señalizar las nuevas capabilities en la UI (frontend/index.html/app.js) queda FUERA de este fix; (b) la variante EN sin topic_en/title_en disponibles no filtra por tema (comportamiento seguro de fail-open); (c) el helper ES sigue reutilizando _STOPWORDS_ES de research vía import — cambiarla allí afecta también a este filtro. |
| 29 | **title_en con fallback ES (§17 #21) se colaba como ancla temática EN de image_search cuando topic_en estaba vacío** — dos puntos de fuga encadenados: (1) editorial.py/autopilot.py rellenaban topic_en con topic/title en ESPAÑOL si no existían campos nativos EN (book_67: title_en=None, description_en=None → topic_en="Todo sobre el café…" en español); (2) el ancla EN resolvía `anchor_topic = data.get("topic_en") or data.get("title_en")`, así que aunque se corrigiera (1), el title_en con fallback ES del §21 volvía a activar el filtro con keywords españolas → mismo descarte masivo que #28 pretendía evitar (log producción: "no anclarse al tema (... [en])" con topic ES). | Reactivación silenciosa del bug §17 #28 en cualquier libro bilingüe sin traducciones nativas; log con formato del fix nuevo pero comportamiento antiguo. | FIXED + VALIDATED + CONFIRMADO EN PRODUCCIÓN (2026-08-26) | Media | book_67 real: job FAILED en quality_gate; diagnóstico dato a dato (BD jobs/book_67.json, payload real con topic_en en español); 3 fixes autorizados: (1) editorial.py image_plan/image_gen — topic_en sin fallback ES (vacío si no hay nativo EN), (2) autopilot.py idéntico, (3) modules/image_search/main.py ~445-457 — anchor_topic EN usa SOLO topic_en (`or ""`), NUNCA title_en; fail-open documentado en #28(b). Tests: test_image_search.py 18/18 (nuevo test_failopen_en_when_topic_en_empty_even_with_spanish_title_en) + test_editorial_bilingual_plan.py 10/10 (nuevo test_image_payloads_no_english_anchor_without_native_en). Variante ES intacta. CONFIRMACIÓN EN PRODUCCIÓN: retry real de book_68 (mismo título/idioma/ratio que book_67, title_en=None, servidor arrancado a las 23:11:27 local, POSTERIOR a los 3 archivos del fix 22:41–22:56 — sin stale-process) tras el fix: quality_gate ya NO reporta ningún descarte por anclaje de idioma (0 warnings "[en]" de tema); el FAIL remanente es motivo DISTINTO (cantidad exacta de imágenes ==5 con slots caídos puntuales 403, ver §17 #30). | Cerrado y confirmado. Residual conocido: libros EN sin traducciones nativas pierden el filtro temático web (fail-open aceptable, §17 #24 sigue acotando SearXNG por idioma); opción futura B = traducir topic on-the-fly vía §17 #21 si reaparece evidencia. |
| 30 | **Con image_search_ratio=1.0 (100% web), la cuota de compensación IA es 0 por diseño (fix 2026-08-25) — si algún slot de descarga web falla (403 Forbidden/timeout), el capítulo queda estructuralmente por debajo de image_count sin ningún colchón, y quality_gate (exige número EXACTO de imágenes por capítulo) falla.** Trade-off de diseño conocido, no bug del pipeline de imágenes. Evidencia real book_68 (retry 2026-08-26 21:11): 8/24 capítulos con 4/5 imágenes por fallos de descarga puntuales (403), overall_status=FAIL pese a que TODO lo demás del gate es PASS (imágenes existen, legibles, metadata presente; capítulos/fuentes/metadatos PASS). Tasks final_quality_control 1360/1361 done, job FAILED quality_gate attempts=2. | Libros con ratio=1.0 y descargas web imperfectas quedan bloqueados en quality_gate aunque el resultado sea funcionalmente válido; requiere reset manual de fases para reintentar (sin garantizar que los slots vuelvan a fallar igual). | OPEN | Baja | Próximo paso SIN autorizar aún — decidir entre: (a) relajar el gate a tolerancia N-1 (o umbral %) cuando image_search_ratio=1.0; (b) reservar una cuota IA mínima incluso con ratio=1.0 como red de seguridad (p.ej. round(num_images*(1-ratio)) >= 1); o (c) dejarlo como comportamiento esperado y documentar que el usuario ajuste el ratio manualmente (mitigación aplicada a book_67: UPDATE books SET image_search_ratio=0.8 → 1 imagen IA de margen/capítulo). Ninguna de las 3 toca código hasta decisión explícita del usuario. **Nueva evidencia (2026-08-28, book_71):** tras desbloquear research (§17 #37), el re-run E2E llegó a quality_gate y falló por déficit de imágenes (check FAIL con origin_phase=image_gen) — mismo patrón de este ítem, sin fix nuevo requerido en esta ronda. |
| 31 | **`persist_chapter_images` (frontend/editorial.py) hace UPDATE incondicional de `chapters.images` en vez de fusionar** — si un capítulo YA persistido se vuelve a ejecutar, las imágenes previas válidas se pierden (sobrescritura, no merge). | NULO en producción normal: el retry automático de fase (`_execute_per_chapter`, `retry_job`, `recover`, core/autopilot.py) respeta siempre `subs.chapters[].status == "PASS"` (core/autopilot.py L.1333-1334 salta PASS; L.745-757 retry de fase conserva capítulos ya persistidos; L.328-350 `retry_job` respeta subs PASS) y NUNCA re-ejecuta un capítulo ya completado. El bug solo se manifiesta si algo fuerza MANUALMENTE la re-ejecución de un capítulo persistido (ej. borrar su entrada en `subs.chapters`), como se hizo en esta sesión con book_67/book_68 para diagnóstico. | CLOSED (2026-08-28) — fix aplicado en commit `a4b7992` (precondición F2 de §17 #36). `persist_chapter_images` ahora LEE el valor previo de `chapters.images` (SELECT + parse JSON, fail-safe a lista vacía si corrupto) y FUSIONA/dedupica por `image_path` ("la nueva gana" en path idéntico = regeneración intencional) en vez de UPDATE plano. Edge cases: `image_paths=[]` conserva las previas (no borra); `image_paths=None` o `overwrite=True` mantienen el overwrite histórico. Ver §19 P3 y §16. | Baja (P3 deuda, no bloqueante) | book_67 (caps 466/476 → 4→3 imágenes tras reset manual v31, mismo patrón que book_68 caps 15/18); código: `frontend/editorial.py` `persist_chapter_images` hace `UPDATE chapters SET images = ?` sin leer el valor previo (L.~377-381); `core/autopilot.py` L.1333-1334 (`_execute_per_chapter` salta PASS), L.7452-757 (retry automático de fase, no re-ejecuta capítulos), L.328-350 (`retry_job` respeta subs PASS). | Si en el futuro se implementa alguna función que re-ejecute capítulos ya persistidos (ej. regeneración manual de un capítulo suelto), diseñar `persist_chapter_images` con merge/dedupe por `image_path` ANTES de habilitar esa función. No antes. |

> **NOTA (2026-08-26):** `book_67` y `book_68` ("Todo sobre el café, descubrimientos, tipos, cafe en el mundo...", 26/08) NO se regeneran ni se tocan sus datos; quedan **INTACTOS como evidencia histórica** del diagnóstico de pérdida de imágenes por sobrescritura (§17 #31) y del trade-off de `image_search_ratio=1.0` (§17 #30) — mismo criterio que book_37/book_39/book_57. Ambos conservan su estado real (job FAILED en quality_gate, capítulos con déficit de imágenes) para auditoría.
>
> **NOTA (2026-08-27):** `book_69` ("Videojuegos, desde el pong al gta 4") ABANDONADO como evidencia histórica — NO se recupera ni se regeneran más capítulos. Motivo: **contaminación masiva de snippets SERP anterior al fix §17 #33** (~20/24 capítulos preexistente al fix; solo cap.503 se regeneró de prueba quedando limpio con fact_check PASS). Mismo criterio que book_37/book_39/book_57/book_67/book_68. Su estado real (job FAILED en fact_check, pool de fuentes limpio a 3) se conserva intacto para auditoría.

para auditoría.
| 32 | **fact_checker no-determinista en la llamada LLM PRINCIPAL** — mismo `chapter_text` producía severity **ERROR/PASS distinta** entre llamadas consecutivas y `quality_gate` flipeaba **FAIL/PASS**. Causa raíz: **ausencia de seed fijo** en la cadena hacia Ollama (`temperature=0.0` sin seed no garantiza reproducibilidad en este despliegue `qwen-agent:latest`/Ollama) — LLM emitía variaciones aleatorias incluso a temperatura 0. Distinto de §17 #25 (gate de re-verificación ERROR subjetivos) y §17 #28/#29 (image_search). Evidencia: **book_69 cap.2 ES, tasks 1476 (Carl=ERROR, Claude=ERROR, gate=FAIL) vs 1477 (Carl=PASS, Claude=PASS, gate=PASS)**, chapter_text idéntico (len=9911, SHA confirmado). | Bloquea book_69 (FAIL) y puede colar contenido fabricado (1477 PASS) o bloquear libros válidos por azar; corrompe confiabilidad del gate. | **FIXED + VALIDATED (2026-08-27)** — seed fijo como **parámetro EXPLÍCITO** en la cadena de providers (no vía env global): `core/providers/base.py` — `LLMProvider.generate()` acepta `seed: Optional[int]=None` (inyecta a `kwargs["seed"]` sólo si no es None; default sin cambios); `core/providers/ollama.py` — `_generate_once` reenvía a `options["seed"]` sólo si no es None; auditoría: `AnthropicProvider` ignora seed silenciosamente (0 providers rotos). Nueva constante `FACT_CHECK_SEED=1337` en `modules/fact_checker/main.py` en las 2 llamadas LLM (`execute()` principal + `_verify_error_consistency`), ambas con `temperature=0.0`. Validación: `pytest tests/test_fact_checker.py -v` → **26 passed**, 0 failed, 0 errors; determinismo real con 2 llamadas offline a `fact_checker.execute()` con payload task 1476 (código prod, sin mocks): Carl=A=B **ERROR**, Claude=A=B **ERROR**, gate=A=B **FAIL** (antes ERROR/PASS→flip); reproduce el veredicto correcto de 1476. | Alta | book_69 (tasks 1476/1477; base.py+ollama.py OUT_OF_SCOPE autorización 2026-08-27; fact_checker/main.py OUT_OF_SCOPE sesiones previas). | book_69 en FAIL en fact_check; 24/24 capítulos writer PASS (ver §23). Próximo: resetear capítulos fallados en fact_check (505 ES) para re-ejecución bajo el fix — ver PARTE B. **CERRADO por completo (2026-08-27): sub-fix de re-anclaje determinista** en `_escalate_fabrication_issue` (nueva `_find_reanchor_source`, n-gramas de 8 palabras contra `source.content`): si un claim marcado "sin soporte" aparece literal en el content de una fuente permitida, se re-ancla (`source_url` recuperado) en vez de escalar a ERROR. Motivo: claims reales de book_69 (Carl Johnson, Claude de GTA) fallaban solo por mal anclaje del LLM, no por fabricación. Validado: **30/30 PASS** en `test_fact_checker.py`, sin regresión en book_59 (§17 #20 intacta). Archivos: `modules/fact_checker/main.py`, `tests/test_fact_checker.py`. |




| 33 | **chapter_writer incrusta snippets de búsqueda crudos sin sintetizar en la prosa final**, produciendo fragmentos gramaticalmente rotos (ej. book_69 cap.503 ES: "Table Tennis era, a todos. Por tanto...", precedido de "6 sept 2024 ... El legado de Table Tennis..." pegado literal). **CONFIRMADO PATRÓN SISTEMÁTICO (2026-08-27, 2/2 regeneraciones de book_69 cap.503 ES con el mismo defecto exacto, misma frase rota "Table Tennis era, a todos..." reaparecida idéntica tras regenerar desde cero — tasks 1480/1482 y 1486).** Ya NO es caso aislado. Correctamente bloqueado por fact_checker (§17 #20), **no es bug de fact_checker**. | Bloquea la recuperación de book_69 (únicamente cap. 503 ES, re-FALLA en fact_check tras regenerar: task 1486, 3 claims ERROR, gate FAIL, job FAILED); el resto del libro (23/24) intacto en PASS. | **FIXED + VALIDATED (2026-08-27).** Causa raíz confirmada en `modules/research/main.py` (`_search_searxng`): SearXNG entrega el `content` como SNIPPET del buscador (fecha "6 sept 2024", "hace N días", truncamiento "..." — evidencia book_69/task_1486: 6/9 fuentes con 153-166 chars, frente a 3 de Wikipedia limpias de 224-254) y se persistía tal cual como si fuera contenido real, que el writer luego copiaba verbatim. **Fix**: nuevo helper `_is_serp_snippet()` + `_SERP_LENGTH_THRESHOLD=250` en `modules/research/main.py`, filtro skip-and-continue en `_search_searxng` (mismo patrón que el denylist §17 #27); `_has_anchor_*`/curación/research NO cambian. **Validación**: 3 tests nuevos en `test_research_searxng.py`; simulación offline confirma 6/6 snippets descartados y 3/3 Wikipedia preservados; **34/34 PASS** en suite focalizada de research (test_research_searxng/sources/multisource/curation). **NO afecta a chapter_writer** (PROTECTED). **Pendiente**: el fix solo protege RESEARCH NUEVO. **book_69 ABANDONADO como evidencia histórica por decisión del usuario (2026-08-27)** — NO se recupera (contaminación de ~20/24 capítulos preexistente al fix; regenerar el libro completo no se considera prioritario). Mismo criterio que book_37/book_39/book_57/book_67/book_68. El fix de código en sí queda **validado y activo para libros nuevos**: prueba directa = cap.503 regenerado de cero con el pool limpio → 1715 palabras, sin snippets, fact_check PASS. — las 6 fuentes ya persistidas en BD en `data/sources` para book_69/cap.503 siguen contaminadas (ids 829, 830, 832, 834, 835, 836); falta decidir vía de recuperación (desasociar de chapter_ids=503 vs re-ejecutar research global) — ver diagnóstico de recuperación. | Media-Alta | book_69 tasks 1480/1482/1486, chapter_id=503 (payload en BD; chapter_text con snippets crudos tipo "6 sept 2024 ...", "hace 6 días ...", "...4 puntos del." y conectores de relleno repetidos incrustados en la prosa; nuevo draft 1486: 1643 palabras ≥1500 pero mismo patrón). | Diagnóstico causa raíz (solo lectura) antes de plantear fix; no recuperar book_69 hasta identificar la capa responsable. |---
| 36 | **Retry tras FAIL de quality_gate no vuelve a la fase de origen real** — retry_job es un reset plano: solo resetea fases con status no-PASS, nunca retrocede sobre fases ya PASS. Si quality_gate falla por un déficit generado en una fase anterior ya PASS (ej. image_gen con imágenes insuficientes, §17 #30), el retry solo re-ejecuta quality_gate, que vuelve a fallar por lo mismo — bucle estéril. quality_control tampoco expone hoy ningún campo estructurado de "fase responsable", solo mensajes de texto libre. | Bucle de retry sin progreso en libros con FAIL de quality_gate causado por fases anteriores (evidencia: book_67/68, §17 #30). Requiere resetear manualmente fases anteriores vía cirugía directa en BD (no soportado por API). | CLOSED (2026-08-28) — las 3 fases del plan completadas: (1) origin_phase estructurado en QC, (2) §17 #31 resuelto como precondición de seguridad, (3) retry_job(from_phase=...) con cascade + wiring API, y Fase 5 (UI) reutilizando el botón Reintentar existente sin crear controles nuevos. Ver §16 y §24. | Media | Diagnóstico de sesión 2026-08-28 (solo lectura): retry_job (core/autopilot.py L.306-354) nunca retrocede sobre fases PASS; QualityControlItem (core/schemas.py L.475-479) solo tiene status+message, sin campo de origen. | Fase 1: añadir origin_phase a QualityControlItem y anotarlo en los ~15 puntos FAIL de quality_control (mapeo directo por check: images→image_gen, sources→research, chapters-text→writer/editor, documents→docx). |
| 37 | **Research falla con 0 fuentes cuando el query/topic es una frase larga** — research consultaba Wikipedia/Wikidata/SearXNG con la idea/título completo del libro sin normalizar; queries-frase largos devuelven 0 resultados en todos los backends y el módulo caía a modo determinista FAIL ("No se obtuvieron fuentes reales.") sin ningún intento de recuperación. | Bucle de retry estéril en cualquier libro cuya idea sea una frase larga (evidencia: book_71, título "Historia de los videojuegos, desde el pong, hasta...gta6"; 2 intentos seguidos tasks 1880/1881 FAIL; Ollama sano y backends HTTP 200, no era un apagón). | FIXED + VALIDATED (E2E CONFIRMADO 2026-08-28) | Media | commit 711874f: helper _shorten_query_for_search (corte en separador de cláusula + filtrado de stopwords reutilizando _STOPWORDS_ES, ~6 palabras significativas) y reintento ÚNICO contra Wikipedia/Wikidata cuando el query crudo da 0; topic/_has_anchor_keyword/denylists/filtro SERP sin cambios. Tests focalizados 32 passed (2 nuevos, mock HTTP). E2E real: book_71 task_id=1883 → status=PASS, execution_mode=llm, fuentes reales obtenidas vía query corto derivado ("historia de los videojuegos" → página de Wikipedia homónima); pipeline upstream completo PASS (planner/research/outline/writer). |


# 18. LIMITACIONES CONOCIDAS

Distinción **LIMITATION** (diseño/entorno) vs **BUG**.

| Limitación | Tipo |
|---|---|
| Requiere **servidor ComfyUI accesible** (`COMFYUI_URL`) + **checkpoints SDXL Base+Refiner descargados** para generación real de imágenes; si no está disponible/apagado, cae a placeholder local automáticamente. | LIMITATION |
| **PDF** fuera de alcance / deuda (pdf_builder). | LIMITATION / OUT OF SCOPE |
| **Dependencia de Ollama** (LLM local) para planner/research/writer/fact_check/editor/image_plan; si no está activo se usa fallback determinista (writer/editor), pero la calidad nominal del LLM se pierde. | LIMITATION |
| Requiere **worker/server activo** (scheduler/ap formativo) para procesar tareas. | LIMITATION |
| Frontend muestra las 10 fases del pipeline (image_plan/image_gen incluidas), pero sin UI para elegir proveedor/modelo de imagen IA (queda el local placeholder). | LIMITATION (UX) — Ver §20 tarea 4 (backlog) |
| El LLM puede devolver continuaciones duplicadas (se detectan y rechazan como avisos, no fallos). | LIMITATION (calidad LLM, acotada) |
| Document Builder no renderiza listas Markdown numeradas como listados nativos de Word (las trata como párrafos). | LIMITATION |
| Textos de interfaz parcialmente en inglés. | RESUELTO (ver §16, FASE 8H.1) — quedan solo términos técnicos/intencionales en inglés (SSE, DOCX, Autopilot, QC, etc.). | LIMITATION → RESUELTO |
| El filtro de relevancia de research (`_keyword_overlap`) se basa en solapamiento léxico de palabras clave, no en relevancia semántica/temática. Puede dar falsos positivos cuando existe coincidencia léxica casual (ej.: existe un lugar real "Dooms, Virginia" cuyo nombre coincide con un título ficticio "Los Dooms", haciendo que artículos geográficos irrelevantes pasen el filtro). Mitigado en profundidad por fact_check, que rechaza afirmaciones sin respaldo real independientemente de si la fuente pasó el filtro de research. No se aborda ahora: requeriría relevancia semántica, cambio de alcance mayor. | LIMITATION |



RESUELTA — ver §19 P3 (ComfyUI reescrito como proveedor real, 2026-08-17). Ficha histórica obsoleta, eliminada 2026-08-23.
# 19. DEUDA TÉCNICA

Priorizada (no estética como P0):

| Prioridad | Deuda | Nota |
|---|---|---|
| **P1** | ~~search_chapter_images sin PAYLOAD_SCHEMAS/OUTPUT_SCHEMAS~~ | **CLOSED** — ver §16. Registrado ImageSearchPayload + reutilizado ImageGenerateOutput, 2026-08-18. images_per_chapter con ratio>0 ya no falla en validate_payload. |
| **P2** | Que `book_planner` emita `author`/`genre`/`language` en la ruta autómata real. **Mejora, no bug**: el sistema ya funciona correctamente sin ello (QC/DOCX aceptan su ausencia por diseño, ver §16). | **CLOSED** — resuelto en FASE 8J.1 (ver §16): `book_planner.execute()` emite `language` (del payload, default "es") y `genre` (inferido keyword-based desde la idea); `author` omitido (no derivable honestamente) |
| **P2** | PDF: renombrar a `book_{book_id}_{lang}.pdf` y validar. | Problema Abierto #3 |
| **P2** | Traducir textos de interfaz que quedan en inglés. | Sec. 13 | CLOSED (ver §16, FASE 8H.1) — textos de interfaz traducidos; lo que queda en inglés es intencional (términos técnicos, marcas, formatos). |
| **P3** | Eliminar código duplicado/inaccesible en `frontend_api.py`. | CLOSED (ver §16, FASE 8H.2). Microdiagnóstico confirmó que no había código inaccesible real, solo imports/comentarios redundantes de bajo riesgo. |
| **P3** | Reconexión SSE robusta si se implementa. | MANUAL_USUARIO §27 |
| **P3** | Conectar un proveedor de imágenes IA real (p.ej. ComfyUI) por defecto. | **CLOSED** — 2026-08-17: `ComfyUiProvider` reescrito (SDXL Base+Refiner real) + fallback validado; timeout externo de fase `timeout_seconds=360` + guard de presupuesto interno (`IMAGE_TOTAL_TIME_BUDGET=330s`) + `COMFYUI_CONNECT_TIMEOUT` corto (10s); fix de `registry._register_defaults` (mover `default=True` a `ComfyUiProvider`) y **flip `DEFAULT_PROVIDER="comfyui"` ACTIVADO POR DEFECTO**. Ver §16 y §8. |
| **P3** | (opcional, no urgente) Extraer helper de validación común para los pares approve/reject y cancel/retry en frontend_api.py (patrón fetch→validate→acción→broadcast repetido, pero con estados/retornos distintos; riesgo medio si se toca, valor bajo) | Diagnóstico 8H.2 §1.3 |
| **P3** | El runner E2E real (`run_e001_editorial.py`) no ejercita el camino real de outline→writer con LLM activo: corre en `chapter_execution_mode=deterministic` con editor `fallback`, por lo que no detecta bugs como outline.sections vacío (8K.1). | `run_e2e_001_editorial.py`; deuda de cobertura E2E — se necesita un test focalizado que invoque el outline con LLM real y verifique `sections` non-empty antes del writer |
| **P2** | Patrón recurrente: fixes de código validados en repo/tests no llegan al proceso real del servidor hasta reinicio manual (visto en 8L.1/8L.2 con `CHAP_FORCE_MIN`, y de nuevo hoy con el fix de research — book_23 corrió con módulo stale pese a que el fix ya estaba en el repo ~14 min antes). No hay verificación automática de que el proceso activo corresponda al código del repo. Sin mecanismo de auto-reload ni check de versión/hash al arrancar. | Catalizador 2026-08-16: reinicio manual requerido para exponer los fixes de research (anchor) + fact_check (gate). | Nueva ocurrencia 2026-08-22: fix de phase['metrics'] en quality_gate/fact_check/research no se reflejó en el servidor vivo (PID 26960 → reiniciado a 24116/19460) hasta reinicio manual, confirmando el patrón. Cuarta ocurrencia evitada proactivamente 2026-08-22 (fix anti-reciclaje de fuentes): se verificó timestamp de arranque vs LastWriteTime ANTES de dar por buena la validación en producción. |
| **P2** | Nueva ocurrencia confirmada del patrón de stale-process (ver fila P2 existente): PID 30336 arrancado 21:36:39 seguía sirviendo modules/quality_control/main.py sin el fix de image_count guardado a las 22:17:30 — el retry de book_38 a las ~21:37 (mismo proceso) repitió el FAIL '!=3' pese al fix ya en disco. Diagnóstico por comparación de timestamps (arranque vs LastWriteTime) confirmó la causa; reinicio a PID 11200 (23:12:05, posterior a los 4 módulos tocados en la sesión) resolvió el problema de inmediato. | Tercera ocurrencia documentada de este patrón en el proyecto (2026-08-16, 2026-08-22 research/fact_check, y ahora 2026-08-22 quality_control). Sigue sin existir mecanismo automático de detección; ver fila P2 original para la propuesta de auto-reload/version-check. |
| **P2** | **CUARTA ocurrencia CONFIRMADA y CERRADA (2026-08-24, book_57):** el job de book_57 ('Las mejores series que se hicieron más virales...', languages="es,en", 19:37:07–20:03:44) se sirvió con el módulo research en memoria ANTERIOR al fix multi-idioma pese a que el orquestador multi-idioma NUEVO sí estaba activo (payloads etiquetados es/en, dos pasadas) — mezcla de versiones en un mismo proceso. Resultado: contaminación temática con fuentes ajenas al tema (The Backyardigans, Liga Mexicana de Béisbol, Historia de la biología, El Chombo — todas es.wikipedia). Confirmado por timestamps: job terminó 20:03 vs archivos del fix escritos 20:49–20:50 (research/autopilot/editorial). Resuelto con reinicio limpio (PID 20360, START 21:33:32 > LWTs) + validación positiva con book_58 (tema neutro 'pulpos', bilingüe): 0 contaminación, filtro de anclaje funcionando con código fresco. book_57 se conserva INTACTO como evidencia histórica (no se regenera), mismo criterio que book_37/book_39. | Diagnóstico solo-lectura 2026-08-24: _has_anchor_keyword actual rechaza las 4 ofensoras (pasa=False; Backyardigans 0 hits, resto 1 hit <2 exigidos) → el fallo era exclusivamente de versión en memoria, no de código vigente. |
| **P3** | Pasada de research 'en' en libros bilingües no produce contenido nativo en inglés cuando el topic/título del libro está en español — la query viaja tal cual (sin traducir/localizar), por lo que en.wikipedia.org no matchea y SearXNG devuelve resultados en español igual que la pasada 'es'. Evidencia: book_58 (tema neutro 'pulpos', sin contaminación ni stale-process, código fresco) → pasada EN devolvió 0/8 fuentes en dominios ingleses, idénticas a la pasada ES (intersección 8/8). Deuda NUEVA, no parte de la P3 de research por idioma ya cerrada. Prioridad media — no bloqueante: el libro EN se genera igualmente con texto inglés natural vía backstop/LLM, pero las fuentes citadas no son nativas. Sin autorización de fix todavía; requiere decisión de diseño (¿traducir el topic antes de la query EN? ¿fallback a query en inglés genérica del género?). **ACTUALIZACIÓN 2026-08-25 (book_62):** el gap SIGUE ABIERTO como P3, pero ya NO puede tumbar el job completo — la pasada 'en' con source_count < min_sources se mitiga a nivel de orquestación con fallback en `_run_research_multilang` (sources_by_lang[secundario]=copia del primario + warning, ver §16). Lo que queda abierto es solo la CALIDAD de las fuentes EN (no nativas), no la disponibilidad del pipeline. | Detectado en validación Frente A 2026-08-24 (book_58, análisis de job.data.sources_by_lang); mitigación orquestacional book_62 2026-08-25 |
| **P2** | Hardening de doc.save() en build_book_docx (referenciado desde §17 #16): guardado sin try/except ni verificación post-save — riesgo de PASS con DOCX corrupto/inexistente. | **CLOSED** (2026-08-26) — ver §16: try/except + RuntimeError propagada + verificación os.path.exists/getsize>0 en modules/document_builder/main.py (~L.840-854); 32/32 PASS; core/autopilot.py sin cambios |
| **P3** | Deuda de DATOS (no de producto), ref. §17 #15: el writer LLM puede seguir fabricando citas APA falsas dentro de draft_es/edited_es en BD (invisible para el lector: document_builder reconstruye la sección de fuentes desde chapters.sources y descarta la cola del LLM). Único caso documentado book_47/ch192, ya borrado de BD; barrido 2026-08-26 (books 65/66) → 0 casos. | Sin acción pendiente — se revisará si reaparece evidencia en producción con datos reales (diagnóstico completo en §17 #15, 2026-08-26) |
| P3 | `image_planner/main.py:233` (`_build_prompt`) sigue usando el string antiguo `"Fotografía editorial, paleta coherente, detalle realista"` como guía de estilo textual para el LLM. No es un bug funcional (no se asigna a ningún campo de salida directamente; solo sobrevive al output si el LLM lo copia literalmente en su JSON), pero es inconsistente con el nuevo default `"realistic"`. | Trazado en sesión 2026-08-17 (fix FASE 8N.1); no corregido por estar fuera del alcance autorizado de esa tarea |
| **P2** | test_research_with_sources_passes (tests/test_quality_gates.py) roto por deriva: monkeypatch inyecta resmod.main.research_web que la refactor multi-fuente de modules/research/main.py ya no usa (cambios sin commit en el working tree de research, detectados 2026-08-22 durante validación del fix de quality_control). No es causado por ningún fix de esta sesión. | CLOSED (2026-08-22) — ver §16. Causa: deriva de contrato, los 3 fakes de research_web en tests/test_quality_gates.py usaban la firma vieja (query, max_sources, timeout) sin los kwargs language/topic añadidos por el refactor multi-fuente. Al invocarse con topic=..., el fake lanzaba TypeError capturado silenciosamente por execute(), y los 3 tests (incluyendo 2 que 'pasaban' aparentemente bien) verificaban en realidad la rama de excepción, no la lógica real del gate. |
| **P3** | generate_image (modules/image_generator/main.py) ignora el parámetro num_images y recorre TODO el image_plan en cada llamada, con skip_existing=True por defecto — esto fue la causa raíz de que la compensación de shortfall necesitara forzar skip_existing=False explícitamente. No es bloqueante hoy, pero es una superficie confusa: cualquier código que asuma 'pedí N, recibo N' está equivocado. | Detectado durante el diagnóstico del fix de dedup (2026-08-22, libro 36). Fuera de alcance de ese fix. Ver §16. |
| **P3** | `persist_chapter_images` (frontend/editorial.py) sobrescribe `chapters.images` en vez de fusionar; pierde imágenes previas válidas si un capítulo ya persistido se re-ejecuta. No bloqueante (el retry automático respeta subs PASS). Ref. §17 #31. | **CLOSED (2026-08-28)** — resuelto por el fix §17 #31 (commit `a4b7992`, precondición F2 de §17 #36): `persist_chapter_images` ahora fusiona/dedupica por `image_path` contra el valor previo en vez de sobrescribir; `image_paths=[]` conserva las previas. La deuda de no-idempotencia ante re-ejecución de capítulo persistido queda eliminada. Ref. §17 #31, §17 #36. |
| **P2** | Acabado DOCX no profesional: TOC sin números de página reales, enlaces markdown sin renderizar en 'Fuentes utilizadas' (aparecen como texto literal [texto](url)), fuentes duplicadas en el listado de un capítulo, numeración de Figura no secuencial entre capítulos, header/footer visible en portada mostrando el nombre de archivo interno, año de copyright hardcodeado. Evidencia: book_37.docx completo. Módulo: modules/document_builder/main.py (OUT_OF_SCOPE). | Detectado en diagnóstico book_37 (2026-08-22) | CLOSED (ver §16, ronda P2 document_builder: A1 año copyright + A2 portada sin header/footer + A3 footer sin filename + B1 hipervínculos reales + B3 numeración de figuras sin huecos, todos FIXED + VALIDATED con 24 passed). **B2 es una mitigación defensiva en el renderer (dedupe de líneas de fuente exactas), no corrige la causa raíz upstream en editor/chapter_writer** — causa raíz abierta como §17 #9. Queda pendiente menor dentro del acabado: TOC sin números de página reales (no abordado en esta ronda). |
| **P3** | docProps created/modified del DOCX quedan en el default de python-docx (2013-12-23T23:15:00Z) al no fijarse explícitamente en document_builder — cosmético, sin relación con el copyright (que sí es correcto). Evidencia: book_48. | Diagnóstico solo-lectura 2026-08-23: core_properties solo fija title/author/subject/comments/language (main.py:686-690); ni `core_properties.created` ni `.modified` aparecen en el archivo |
| **P3** | Títulos de capítulo generados por el fallback determinista de book_planner (`_short_idea_title` + "- Parte N") son legibles pero genéricos — los 24 capítulos de un mismo libro comparten la misma base de texto, diferenciados solo por el número | Mejora sobre el bug anterior (idea completa duplicada), no una solución de calidad editorial; solo el path LLM real del planner genera títulos temáticamente distintos por capítulo. No bloqueante. Evidencia: book_55 |
| **P3** | Research no está parametrizado por idioma: en libros EN sigue consultando es.wikipedia.org con el título EN como query (sin backend en-nativo). Pasa el gate y no bloquea, pero las fuentes no son nativas en inglés. | **CLOSED** — 2026-08-24: fix aplicado en `modules/research/main.py` (capa de fetch parametrizada por `language`: `_wiki_search`, `_wiki_extract`, `_wiki_rest_summary`, `_backend_wikipedia`, `_backend_wikidata`; hosts es/en.wikipedia.org), en `core/autopilot.py` (`_run_research_multilang`: research se invoca 1 vez POR IDIOMA en libros bilingües; fuentes fusionadas y deduplicadas por URL en `job.data.sources`, desglose por idioma en `job.data.sources_by_lang`) y en `frontend/editorial.py` (payload de research lleva el idioma activo; writer de cada idioma recibe SOLO las fuentes de su idioma). Aclaración: `_deterministic_curate()` NO requirió cambios — es un ranker puro sobre candidatos que ya llegan en el idioma correcto desde la capa de fetch, así que la ruta LLM (RESEARCH_USE_LLM=1) Y el backstop determinista quedan cubiertos. Ver §16 y §24. |




# 20. ROADMAP

```text
COMPLETADO   — pipeline editorial → document_builder → DOCX; QC; backstop determinista
EN CURSO     — mantener green (diagnóstico 8E.8 cerrado); nada activo pendiente de implementación mayor
SIGUIENTE    — Problemas Abiertos #1 y #2 cerrados (8F.1/8F.2, ver §16). Sin bloqueantes activos; deuda menor en §19.
FUTURO       — PDF estable, imágenes IA reales, traducción UI; selector de proveedor de imagen IA; portada KDP; confirmar generación EN (ver tareas 4-6)
OUT OF SCOPE — PDF (deuda), modificaciones a módulos OUT_OF_SCOPE sin nueva evidencia
```

### Tareas concretas (objetivo/archivos/riesgo/dependencia/criterio)

1. **Cerrar puente Research→Writer→BD→QC (#1)**
   - Objetivo: persistir fuentes en `chapters.sources` para que QC las lea en el autómata real.
   - Archivos probables: `modules/chapter_writer/main.py` (PROTECTED), `core/database.py`, `frontend/editorial.py`.
   - Riesgo: bajo-medio; toca PROTECTED → requiere aprobación.
   - Dependencia: ninguna.
   - Criterio: re-ejecutar E2E → `qc_source_checks` PASS en autómata real; suite sigue verdes.
2. **Alinear metadata (author/genre) en autómata (#2)**
   - Archivos probables: `modules/book_planner/main.py` (OUT_OF_SCOPE → aprobación) o la ruta create_book.
   - Criterio: QC "Metadatos completos" PASS en autómata real.
3. **PDF sin colisión (#3)** — cuando se retome PDF (fuera de alcance ahora).
4. **Selector de proveedor de imagen IA en el front**
   - Objetivo: permitir elegir desde el front qué proveedor/modelo de generación de imágenes usar (hoy fijo a ComfyUI SDXL Base+Refiner vía DEFAULT_PROVIDER, con fallback a local placeholder).
   - Contexto: cierra la fila relacionada de §18 LIMITACIONES CONOCIDAS ("Frontend muestra las 10 fases... pero sin UI para elegir proveedor/modelo de imagen IA") — referencia cruzada añadida allí ("Ver §20 tarea 4").
   - Archivos probables: `frontend/index.html`, `frontend/app.js`, `frontend/editorial.py`, `core/image_providers/registry.py`, posible columna nueva en books (`image_provider`).
   - Riesgo: medio — toca `core/image_providers/`, código sensible ya tocado con autorización puntual en el fix de ComfyUI de 2026-08-17.
   - Dependencia: ninguna técnica bloqueante.
   - Criterio: usuario elige proveedor desde el front, se persiste en books, autopilot lo respeta en la fase image_gen.
   - Estado: NOT_IMPLEMENTED (backlog, sin diseño)
5. **Generación de portada exportable para Amazon KDP**
   - Objetivo: generar un archivo de imagen de portada independiente (no solo la portada de texto embebida en el DOCX que ya existe vía `_add_cover`), apto para subir a KDP como ebook (formato JPEG/TIFF, proporción ~1.6:1). Para tapa blanda en papel haría falta además portada+lomo+contraportada en un único archivo, cuyas dimensiones dependen del nº de páginas.
   - Archivos probables: módulo nuevo o extensión de `document_builder`/`image_generator`; posible endpoint API nuevo.
   - Riesgo: bajo-medio (funcionalidad nueva, no debería tocar el pipeline existente si se implementa como módulo aparte).
   - Dependencia: podría apoyarse en image_generator/ComfyUI para el arte de fondo.
   - Criterio: pendiente de definir — falta especificar requisitos exactos de KDP (dimensiones, DPI, ebook vs tapa blanda) antes de diseñar la solución.
   - Estado: NOT_IMPLEMENTED (backlog, sin diseño, requisitos por definir)
6. **Confirmar/cerrar generación de libro en inglés end-to-end**
   - Objetivo: confirmar si el pipeline real genera capítulos en inglés o si "generar en inglés" nunca se ejecuta en producción real pese a existir piezas del modelo de datos para ello.
   - Contexto: existen `chapters.draft_en/edited_en`, la capability `write_chapter_en` en chapter_writer, y un módulo `translator/` (translate_es_en/translate_en_es). Pero NINGUNA fila de §4 (AUTOPILOT_PHASES), §14 (tests) ni §15 (evidencia E2E) confirma que la rama EN se invoque alguna vez en el autómata real. Es una zona gris del propio documento, no un bug confirmado.
   - Primer paso obligatorio (antes de cualquier fix): diagnóstico de solo lectura — en `core/autopilot.py`, ¿existe lógica condicional por `books.languages` para elegir write_chapter_es vs write_chapter_en? ¿Se invoca translator en algún punto del Autopilot real, o es un módulo standalone sin wiring? Reportar solo hallazgos, sin tocar código todavía.
   - Archivos probables (según resultado del diagnóstico): `core/autopilot.py`, `frontend/editorial.py`; `modules/chapter_writer/main.py` es PROTECTED (no tocar sin autorización); `modules/translator/main.py` es OUT_OF_SCOPE (requiere autorización).
   - Riesgo: depende del resultado del diagnóstico — desconocido todavía.
   - Dependencia: ninguna.
   - Criterio: primero entregar el informe de diagnóstico; la implementación (si hace falta) se decide después, no ahora.
   - Estado: IMPLEMENTED + VALIDATED (2026-08-24).
     - WHY: el diagnóstico previo confirmó que no existía wiring; decisión del arquitecto = opción (b), escritura NATIVA en inglés vía write_chapter_en seleccionado por books.languages, sin fase de traducción.
     - WHAT/FILES:
       * core/autopilot.py — nuevo helper `_resolve_writer_capability(book)` (L72-89, tolerante a str "en"/"en,x" y listas; fallback "es"); `_run_single` resuelve dinámicamente la capability de la fase writer según books.languages (L834-843); `_persist_chapter` escribe draft_en/draft_es según idioma real del libro (L1103-1114); rama editor persiste edited_en para libros EN (L1123-1136). AUTOPILOT_PHASES y el orden de fases NO se mutan.
       * frontend/editorial.py — helper `_is_english_language()` (L435-439); `build_payload` selecciona edited_en/draft_en vs edited_es/draft_es según idioma del libro en los 4 casos que leían columnas _es hardcodeadas (fact_check L529, editor L541, image_plan L552, image_gen L564); `_build_book_dict` expone claves reales edited_en/draft_en y filtra capítulos vacíos por el campo del idioma activo (L628-674) — sin esto, document_builder recibía 0 capítulos para libros EN.
       * modules/document_builder/main.py — ya bilingüe (_UI_STRINGS por language, FASE previa); SIN cambios en este turno.
     - VERIFICATION: tests focalizados 65/65 PASS (tests/test_autopilot_editorial.py, test_editorial_panel.py, test_editorial_metadata.py, test_autopilot_document_output.py + tests/test_autopilot_writer_en.py NUEVO con 5 tests: matriz de resolución de capability, executor real EN→write_chapter_en / ES→write_chapter_es, build_payload lee _en en las 4 fases, editor persiste por idioma, _build_book_dict+DOCX render EN). E2E REAL: tools/dev/archive/verify_e2e_en_book.py (BD aislada, ejecutor de producción load_modules()+capabilities_map(), CHAP_USE_LLM=0 determinista, research HTTP real) → job COMPLETED, 10/10 fases PASS; chapters.draft_en = 1622 palabras sin placeholders ni español (backstop _DET_*_EN); output/docx/book_1_en.docx generado con "Table of Contents" (sin "Índice") y cuerpo EN ("printing press"/"Gutenberg"). Evidencia archivada: tools/dev/archive/e2e_en_book_evidence.log.
     - RESULT: IMPLEMENTED + VALIDATED. Limitación residual: research no parametrizado por idioma (ver nueva deuda P3 en §19). Suite completa del checkpoint: 673 passed, 4 failed (exclusivamente tests stale de prompt-fuentes en test_chapter_writer.py — nuevo problema #19 en §17, sin relación con los módulos tocados), 1 skipped.
   - `core/autopilot.py`: AUTOPILOT_PHASES fija la fase writer a capability="write_chapter_es" (hardcoded, línea ~58); "writer_en" existe como id reconocido en PER_CHAPTER_PHASES pero NUNCA se instancia como fase real; _run_single usa phase["capability"] directamente, sin ninguna rama condicional por books.languages.
   - `translator` (translate_es_en/translate_en_es): registrado en PAYLOAD_SCHEMAS/OUTPUT_SCHEMAS (core/schemas.py) pero SIN fase propia en AUTOPILOT_PHASES ni caso en build_phase_payload — módulo huérfano del pipeline.
   - Frontend: sin selector de idioma en index.html; app.js::createNewBook() nunca envía "language" en el POST; editorial.py::create_book() persiste siempre default "es" en books.languages.
   - Conclusión: para generar en inglés hace falta diseño nuevo — pendiente de decisión del arquitecto entre (a) traducir edited_es→en vía translator tras el pipeline es, o (b) escribir nativo con write_chapter_en seleccionado por books.languages. Ninguna se ha implementado. **(RESUELTO 2026-08-24: elegida la opción (b), IMPLEMENTED + VALIDATED — ver Estado.)**
   - Hallazgo adicional (diagnóstico solo-lectura, arquitecto, 2026-08-24): frontend/editorial.py::build_payload lee chapter.get('edited_es') or chapter.get('draft_es') hardcodeado a columnas _es en 4 casos (fact_check línea ~518, editor línea ~530, image_plan línea ~541, image_gen línea ~553), ignorando el idioma real del capítulo. image_planner/main.py::_build_prompt también ignora el campo language del payload (prompt visual siempre en español). Ninguno de los dos es bloqueante hoy (no hay libros EN en producción); quedan como parte del mismo diseño pendiente de la tarea 6, no requieren fix aislado.


# 21. ARCHIVOS PROTEGIDOS

Fuente: `tools/dev/config.py` + `tools/dev/security.py` (real, vigente).

### NO TOCAR (PROTECTED_FILES)
- `modules/chapter_writer/main.py` — protegido; solo cambios aprobados (fase 7.9D.7).
- `tests/` — NO modificar tests para forzar PASS (regla de validación #1).

### TOCAR SOLO CON AUTORIZACIÓN (OUT_OF_SCOPE_MODULES)
- `research`, `fact_checker`, `editor`, `document_builder`, `quality_control`,
  `pdf_builder`, `image_generator`, `image_planner`, `book_planner`, `translator`,
  `text_summarizer`, `word_counter`, `mcp_demo`, `mcp_external`.

> `modules/research/main.py` fue modificado con autorización explícita del
> usuario en FASE 8I.1 (multi-fuente + curación LLM) — ver §16/§24.
>
> `modules/chapter_writer/main.py` fue modificado con autorización explícita del
> usuario el 2026-08-23 (fix completo de la regresión de duplicación entre capítulos,
> §17 #7/§16) — mismo patrón que la autorización previa del fix seed=0.
>
> `modules/image_search/main.py` fue modificado con autorización explícita del
> usuario el 2026-08-26 (capabilities ES/EN nativas §17 #28 + aislamiento del ancla
> EN de title_en §17 #29) — ver §16/§24.
> `core/providers/base.py` y `core/providers/ollama.py` fueron modificados con
> autorización explícita del usuario el 2026-08-27 (fix de determinismo del fact_checker, §17 #32) — cambio aditivo (parámetro opcional `seed` en `LLMProvider.generate()`; `OllamaProvider._generate_once` lo reenvía a `options["seed"]` sólo cuando no es None); **no afecta** a writer/editor/research/etc. (default `seed=None` preserva comportamiento actual).

### LIBREMENTE MODIFICABLE (ALLOWED_AUTO_EDIT_DIRS)
- `tools/` (infraestructura de desarrollo).

### Reglas
- `is_protected(path)` → True si cae bajo PROTECTED_FILES.
- `assert_change_permitted(path)` → True solo bajo `ALLOWED_AUTO_EDIT_DIRS` (`tools/`).
- Validaciones (VALIDATION_RULES): no forzar PASS; no reducir requisitos; no desactivar
  Quality Gate; no declarar PASS sin ejecutar; registrar todo cambio con WHY/WHAT/FILES/VERIFICATION/RESULT.

> **No asumir restricciones que ya no estén vigentes:** las listas anteriores son las
> actuales en `config.py`. El Frontend y `run.py`, `run_e2e_001_editorial.py` y la
> infraestructura de datos NO están en OUT_OF_SCOPE (modificables con cuidado).



# 22. REGLAS OPERATIVAS PARA OTRAS IAs

## HOW AN AI MUST WORK ON THIS PROJECT

1. Leer primero **`PROJECT_MASTER_STATUS.md`**.
2. Comprobar el **código actual** antes de modificar (no fiarse de documentos).
3. No asumir que un documento antiguo representa la realidad.
4. **Código + tests + E2E tienen prioridad** sobre estados históricos.
5. No ejecutar suites completas innecesariamente (usar comprobaciones focalizadas).
6. Para un bug localizado:
   ```text
   microdiagnosis
      ↓
   fix mínimo
      ↓
   tests del módulo
      ↓
   E2E focalizado
   ```
7. Reservar la suite completa para checkpoints de integración.
8. No modificar componentes protegidos (sección 21) sin autorización.
9. No hacer refactors no relacionados.
10. No tocar código sano sin evidencia.
11. No declarar PASS sin evidencia.
12. Diferenciar **IMPLEMENTED** de **VALIDATED**.
13. Antes de una modificación grande, explicar: causa / alcance / archivos / riesgo / tests.
14. Mantener cambios mínimos.
15. Actualizar **este documento** después de cambios importantes.


# 23. CHECKPOINT ACTUAL

```text
CURRENT CHECKPOINT
PROJECT STATUS:          KNOWN_GOOD (verificado en state.json/PROJECT_STATUS.md 2026-08-14 23:23:41)
PIPELINE STATUS:         Activo; 8/8 etapas PASS en E2E real; AUTOPILOT_PHASES = 10 fases
EDITOR:                  IMPLEMENTED + VALIDATED (fallback determinista; E2E fallback PASS)
WRITER:                  IMPLEMENTED + VALIDATED (backstop determinista; E2E 1668w ≥1500, sin placeholders). Refactor por idioma 2026-08-25: _build_prompt/_elaborate_fact_deterministic/_elaborate_fact_pair_deterministic/_deterministic_section_paragraphs/_build_section_continuation_prompt/_fallback_chapter separados en variantes _es/_en puras con wrappers de dispatch; cero ramas `if language` fuera de los 6 wrappers; EN con esqueleto íntegro en inglés — ver §16/§24
RESEARCH:                IMPLEMENTED + VALIDATED (multi-fuente Wikipedia es/en + Wikidata + curación LLM opcional con fallback determinista, 8I.1); gate de orquestación corregido (8H.3). Fact_checker: ERROR subjetivos del LLM ahora pasan segunda pasada de consistencia (fix §17 #25, 2026-08-25 — ver §16/§24) Cierre §17 #37 (2026-08-28): recuperación vía query corto derivado cuando el query crudo da 0 candidatos — FIXED + VALIDATED E2E (book_71 task 1883, commit 711874f).
IMAGES:                  Orquestación/persistencia/inserción IMPLEMENTED; proveedor activo = COMFYUI (SDXL Base+Refiner, IA REAL) default desde 2026-08-17; fallback a local si no responde; image_search_ratio: split IMPLEMENTED+VALIDATED en autopilot, BLOCKED para ratio>0 real por falta de schema en core/schemas.py; search_chapter_images YA registrado en PAYLOAD_SCHEMAS/OUTPUT_SCHEMAS (P1 cerrado 2026-08-18) — ratio>0 desbloqueado a nivel de validación de schema (aún sin exponer en el front).; image_search_ratio YA seleccionable desde el front (FASE 8N.3, 2026-08-18) — ciclo completo backend+front cerrado. Nota 2026-08-22: el split ahora deduplica por image_path (_dedupe_by_path) y fuerza generación nueva en la compensación (comp_payload['skip_existing']=False), corrigiendo la regresión del fix de shortfall que duplicaba la misma ruta en chapters.images (libro 36)., quality_gate ahora valida contra book.image_count real (fix confirmado en producción, book_38 COMPLETED con DOCX real). Compensación de shortfall en _run_image_gen_split ahora respeta cuota máxima de IA derivada de image_search_ratio (fix 2026-08-25, ver §16) — antes ignoraba el ratio. Query de búsqueda y prompt de generación IA ahora respetan idioma del libro para libros EN (título title_en, filtro language en SearXNG, prompt SDXL en inglés — fix 2026-08-25, ver §16) — cierra §17 #24. persist_chapter_images filtra rutas locales huérfanas (placeholder sin metadata por re-ejecución del capítulo — fix 2026-08-25, ver §16) — cierra §17 #26. Capabilities nativas por idioma para imágenes 2026-08-26: search/generate_chapter_images_es/_en registradas en schemas y resueltas por _resolve_image_capabilities según books.languages (patrón writer_en); query y anclaje temático nativos por idioma (_has_anchor_keyword_img con stopwords ES reutilizadas de research / EN propias, query EN desde title_en/chapter_text_en), payload EN con topic_en/title_en desde autopilot/editorial — cierra §17 #28.
BOOK PLANNER:               fallback determinista corregido 2026-08-24 (títulos de capítulo cortos, sin idea baked-in — ver §16); max_tokens de la llamada LLM principal ahora escala con target_chapters (piso 2000, techo 6000 — fix 2026-08-25, ver §16) — cierra §17 #22.
DOCUMENT BUILDER:        IMPLEMENTED + VALIDATED (book_{book_id}_{lang}.docx; comments[:255]; sección de fuentes reconstruida determinísticamente desde chapters.sources desde 2026-08-23, ya no depende del texto del LLM — ver §16/§17 #9/#13/#14; UI bilingüe _UI_STRINGS por language desde FASE generación EN). Hardening de guardado 2026-08-26: doc.save() con try/except + verificación post-save (exists/getsize>0), RuntimeError propagada — cierra §17 #16 (ver §16/§19/§24)
GENERACIÓN EN:           IMPLEMENTED + VALIDATED (E2E real 2026-08-24, ver §20 tarea 6). Research multi-idioma para libros bilingües (es,en): IMPLEMENTED + VALIDATED 2026-08-24 — una pasada de research POR idioma (es/en.wikipedia), fuentes separadas por idioma en job.data.sources_by_lang; writer EN recibe fuentes nativas EN (prompt LLM y backstop determinista sin hechos mezclados) — ver §16/§19 P3 CLOSED/§24
LAYOUT:                  IMPLEMENTED + VALIDATED (5 presets + aliases + overrides)
FRONT:                   IMPLEMENTED (10 fases; textos de interfaz traducidos, 8H.1; sin UI de proveedor de imagen IA)
TESTS:                   Checkpoint suite completa 2026-08-28 (post-commits §17 #35(F1-F3)/#36(F1-F5)/#37): 786 passed, 0 failed, 0 errors, 1 skipped (315.30s, tests/ + modules/). Histórico: checkpoint 2026-08-27 (764 passed).
E2E:                     PASS (e2e_001_report.json status=completed; 8/8; DOCX PASS; QC PASS)
DOCX:                    PASS (output/docx/book_1001_es.docx)
PDF:                     OUT OF SCOPE / DEUDA (book_{language}.pdf sin book_id)
MAIN BLOCKERS:           Ninguno bloqueante. La mitigación de fabricación histórica (§17 #20, book_59) queda CERRADA 2026-08-24 (3 capas: fact_checker ERROR+gate bloqueante, writer anti-invención, planner anclado a fuentes — ver §16/§24), sin bloqueantes activos. Problemas Abiertos #1, #2 y #4 cerrados (ver §16: 8F.1/8F.2/8F.4/8J.1). Deuda de cobertura de orquestación editor/image_gen cerrada en 8G.2 (ver §16/§19). Contaminación temática (§17 #6) CERRADA 2026-08-23. Fuentes duplicadas/fabricadas/omitidas en DOCX (§17 #9/#13/#14) CERRADAS a nivel de producto 2026-08-23; deuda residual de datos sucios en BD (writer) documentada, no bloqueante. #11/#12/#18 (relevancia temática de imágenes, falso warning de metadata, webp no insertable) CERRADOS 2026-08-24, sin bloqueantes activos. Snippets de redes sociales como fuente (§17 #27) CERRADO 2026-08-26 (denylist _SOCIAL_MEDIA_DENYLIST en research — ver §16/§24). Tests stale de chapter_writer (§17 #19) CERRADOS 2026-08-26: 165 passed, 0 failed, 1 skipped en test_chapter_writer.py + test_chapter_writer_placeholder.py + test_runner_e2e_001.py — sin bloqueantes activos. Hardening doc.save() (§17 #16) CERRADO 2026-08-26 (try/except + verificación post-save en document_builder, 32/32 PASS — ver §16/§19/§24). Sin bloqueantes activos.
| #35 | **Media** | Fail-fast a nivel de JOB completo cuando falla el gate de fact_check de UN solo capítulo — el job entero se marca FAILED y ningún capítulo entra al DOCX, aunque el resto del libro esté sano. Evidencia: book_70 (24 capítulos, 23 sanos) FAILED por 1 sola claim ERROR en cap.538 (Claude/GTA: dato real pero la fuente Wikipedia disponible no cubre el detalle completo — no es fabricación, es accuracy parcial). Decisión de producto (2026-08-27, usuario): el pipeline debería diferenciar severidad de ERROR en vez de tratarlos todos igual:
  (a) fabricación estructural (`_has_fabrication_signature`: fecha+cifra+nombre propio inventado, patrón book_59/§17 #20) → DEBE seguir bloqueando duro, sin excepción (job FAILED, capítulo fuera del DOCX). Esta barrera NO se toca ni se relaja.
  (b) accuracy parcial (claim real, fuente disponible no la cubre del todo) → NO debe bloquear el job entero; el capítulo debe poder avanzar y entrar al DOCX con un marcador visible (nota al pie tipo 'contenido no verificado completamente'), aislado por capítulo, sin tumbar el resto del libro.
Alcance: toca fact_checker (taxonomía de ERROR), core/autopilot.py (gate per-chapter vs job completo), document_builder (marcador visible), posiblemente schema de chapters (nuevo estado tipo PASS_WITH_WARNING). Diagnóstico de arquitectura de solo lectura en curso antes de proponer implementación — ver Estado. | **CERRADO — Fase 1 (error_type) + Fase 2 (gate diferenciado + persistencia `quality_status` en BD) + Fase 3 (marcador visible `⚠️ Contenido no verificado completamente` en DOCX) VALIDADAS 2026-08-27.** Ver §16 fila #35 para detalle completo. | core/fact_checker, core/autopilot.py, frontend/editorial.py, core/database.py, core/document_builder/builder.py | — | — |
NEXT RECOMMENDED ACTION:§17 #36 CERRADO POR COMPLETO (Fases 1-5). Sin bloqueantes activos en el frente de retry/reset.

# 24. CHANGELOG RESUMIDO (hitos)
# 24. CHANGELOG RESUMIDO (hitos)
FASE §17 #29 CONFIRMADO EN PRODUCCIÓN (book_68 retry 21:11, servidor 23:11>22:41-56): quality_gate ya NO descarta por anclaje de idioma (0 warnings "[en]"); FAIL remanente es de cantidad exacta de imágenes ==5 (nuevo §17 #30, Baja) — bug del ancla cerrado/confirmado / 2026-08-26
FASE fix fallback ES en topic_en + aislamiento del ancla EN de title_en (cierre §17 #29, book_67) / 2026-08-26
FASE fix fallback ES en topic_en + aislamiento del ancla EN de title_en (cierre §17 #29, book_67) / 2026-08-26
- problema: pese al fix §17 #28, book_67 seguía descartando en masa candidatos web EN — log "no anclarse al tema (... [en])" con topic en español; causa doble: (1) editorial.py/autopilot.py rellenaban topic_en con topic/title ES cuando no había nativos EN (book_67: title_en/description_en NULL), y (2) el ancla EN de image_search caía a title_en (`topic_en or title_en`), que lleva el fallback ES del §17 #21
- solución autorizada en 3 puntos: topic_en sin fallback ES en editorial.py (image_plan+image_gen) y autopilot.py (vacío si no hay nativo EN → fail-open #28b); anchor_topic EN usa SOLO topic_en (`or ""`, NUNCA title_en — modules/image_search/main.py ~445-457); variante ES intacta
- validación: test_image_search.py 18/18 (nuevo test_failopen_en_when_topic_en_empty_even_with_spanish_title_en) + test_editorial_bilingual_plan.py 10/10 (nuevo test_image_payloads_no_english_anchor_without_native_en); suite completa NO ejecutada
- estado: FIXED + VALIDATED (2026-08-26); producción pendiente de confirmar tras el reset del job book_67 (image_plan/image_gen/quality_gate → PENDING, backup book_67.json.bak_pre_reset_28)


FASE fix capabilities ES/EN nativas para image_search/image_generator + routing autopilot/editorial (cierre §17 #28, book_67) / 2026-08-26
- problema: query y anclaje temático de imágenes eran únicos y monolingües (reutilizando _has_anchor_keyword de research): en libros bilingües/EN se descartaban en masa candidatos web on-topic porque keywords ES nunca matcheaban slugs/títulos EN (evidencia book_67: unsplash/pexels «coffee» descartados con topic español)
- solución en 2 pasos autorizados: (1) modules/image_search/main.py e image_generator/main.py — capabilities search_chapter_images_es/_en y generate_chapter_images_es/_en, helper _has_anchor_keyword_img(topic, cand, language) con stopwords por idioma (ES reutilizadas de research, EN propias) y MISMO umbral que research; query EN nativa desde title_en/chapter_text_en; (2) core/schemas.py (4 capabilities registradas reutilizando clases existentes + topic_en opcional), core/autopilot.py (_resolve_image_capabilities según books.languages, payload EN con topic_en/title_en, AUTOPILOT_PHASES sin cambios), frontend/editorial.py (topic_en/title_en en image_plan/image_gen)
- validación: paso 1 → 7 tests nuevos image_search (book_67: candidato café EN ancla con topic EN y NO con topic ES; regresión §17 #11 comicvine descartada en ambas variantes) + 2 image_generator; paso 2 → 25 passed, 0 failed en test_schemas_image_search.py + test_autopilot_persist_chapters.py + test_editorial_bilingual_plan.py. Suite completa NO ejecutada
- estado: FIXED + VALIDATED (2026-08-26); UI de las nuevas capabilities queda como paso opcional 3 fuera de este fix


FASE diagnóstico §17 #15 (citas fabricadas) — downgraded, sin acción / 2026-08-26
- diagnóstico solo lectura: las citas APA fabricadas SÍ viajan en chapter_text al fact_checker, pero su prompt no clasifica bibliografía como claim; _has_fabrication_signature cubriría el caso solo si existiera issue previo "sin soporte" (nunca se genera para bibliografía)
- prevalencia: único caso documentado (book_47/ch192) ya borrado de la BD; barrido de la BD actual (books 65/66) → 0 casos; impacto en lector nulo desde el fix determinista de fuentes en document_builder (2026-08-23)
- decisión: sin fix — deuda de datos P3 en §19 (ref. §17 #15 DOWNGRADED); se reevalúa si reaparece evidencia en producción

FASE hardening doc.save() en document_builder (cierre §17 #16) / 2026-08-26
- problema: build_book_docx llamaba a doc.save() sin try/except ni verificación posterior — un fallo a medias del guardado (disco lleno, permiso denegado, proceso interrumpido) podía reportar éxito con un DOCX corrupto o inexistente
- solución: try/except alrededor de doc.save() que propaga RuntimeError clara ("No se pudo guardar el DOCX en '<path>'"), + verificación post-save os.path.exists/getsize>0 con RuntimeError si el fichero falta o pesa 0; mismo contrato de excepción que validate_payload, core/autopilot.py SIN CAMBIOS; resto de la lógica intacta
- validación: 2 tests nuevos en modules/document_builder/tests/test_document_builder.py (test_docx_save_failure_propagates_as_error con mock OSError(28), test_docx_save_success_path_unchanged regresión) → 32/32 PASS (3.02s)
- estado: FIXED + VALIDATED (§16 / §17 #16 CLOSED / deuda §19 CLOSED)

FASE fix denylist redes sociales en research (§17 #27, book_66) / 2026-08-26
- problema: SearXNG devolvía posts de TikTok/Instagram con metadata de engagement como fuentes citables; el writer LLM los copiaba verbatim en el capítulo y el fact_checker bloqueaba correctamente (patrón fecha+nombre+cifra → ERROR estructural). Evidencia: book_66 / chapter_id=458 / task_id=987 (sources[8] tiktok.com/@teban_cometacos, sources[9] instagram.com/reel/...)
- solución: `_SOCIAL_MEDIA_DENYLIST` (5 dominios) + helper `_is_social_media()` (netloc sin www., comparación por contenido para subdominios) en modules/research/main.py; descarte silencioso de candidatos sociales en el bucle de recolección de `_multi_source_search`, ANTES del dedupe y de la curación/ranking. Sin cambios en _keyword_overlap/_has_anchor_keyword ni resto de lógica
- validación: 2 tests nuevos en tests/test_research_sources.py (reproducción book_66 + unitario del helper); pytest test_research_sources.py + test_research_multisource.py + test_research_curation.py → 30/30 PASS (3.93s)
- estado: FIXED + VALIDATED (§16 / §17 #27 CLOSED)

FASE cierre §17 #19 — tests obsoletos de chapter_writer actualizados / 2026-08-26
- problema: 4 tests stale en tests/test_chapter_writer.py esperaban que el prompt del writer instruyera generar '## Fuentes'/'## Sources used', comportamiento eliminado intencionalmente (fix document_builder §16); además uno tenía un bloque de aserciones huérfano que garantizaba NameError
- solución: solo tests/test_chapter_writer.py (main.py PROTECTED sin tocar): aserciones actualizadas al comportamiento actual (prompt NO instruye generar sección de fuentes y SÍ contiene la regla "Do not include any sources..."); bloque huérfano eliminado; test_prompt_no_content_after_sources reescrito contra los delimitadores reales ('No añadas ninguna sección después de `Conclusión`' ES / 'Do not add any section after `Conclusion`' EN)
- validación: pytest test_chapter_writer.py + test_chapter_writer_placeholder.py + test_runner_e2e_001.py → 165 passed, 0 failed, 1 skipped (48.49s)
- estado: FIXED + VALIDATED (§17 #19 resuelto)

FASE fix consistencia fact_checker ERROR subjetivo (book_64/book_65) / 2026-08-25

FASE fix determinismo fact_checker: seed fijo FACT_CHECK_SEED=1337 en la cadena de providers (§17 #32, book_69 tasks 1476/1477) / 2026-08-27
- problema: temperature=0.0 SIN seed no garantiza reproducibilidad en qwen-agent/Ollama: mismo chapter_text (book_69 cap.2 ES task 1476) produjo severity ERROR/PASS distinta y gate FAIL/PASS entre llamadas (1476=ERROR/FAIL vs 1477=PASS/PASS, chapter_text idéntico len=9911).
- solución: (1) core/providers/base.py — LLMProvider.generate() acepta seed: Optional[int]=None, inyecta a kwargs sólo si no es None (default sin cambios); (2) core/providers/ollama.py — _generate_once reenvía seed a options[seed] sólo si no es None (AnthropicProvider ignora seed silenciosamente, auditoría 0 providers rotos); (3) modules/fact_checker/main.py — FACT_CHECK_SEED=1337 en las 2 llamadas LLM (execute() principal + _verify_error_consistency), con temperature=0.0 preexistente.
- validación: pytest tests/test_fact_checker.py -v → 26 passed, 0 failed, 0 errors; 2 llamadas offline reales a fact_checker.execute() con payload task 1476 (código prod, sin mocks): Carl=ERROR/ERROR, Claude=ERROR/ERROR, gate=FAIL/FAIL (A==B True); reproduce el veredicto correcto de 1476 (fabricación estructural real, sin fuente).
- estado: FIXED + VALIDATED (2026-08-27). book_69 recovery = próximo paso.
- problema: el LLM del fact_checker asignaba severity=ERROR bloqueante por juicio subjetivo de exactitud en claims sin firma estructural ni marcador de falta de soporte (§17 #20 no las cubría), con veredicto inestable entre reintentos — book_65 "café Liberica" y book_64 cafés de Madrid agotaban reintentos → fase FAILED
- solución: _verify_error_consistency() en modules/fact_checker/main.py — segunda pasada LLM binaria (ERROR/DEFENDIBLE, FACT_CHECK_CONSISTENCY_TIMEOUT=20s) SOLO para ERROR subjetivos en execution_mode='real'; sin confirmación o ante fallo/timeout degrada a WARNING (fail-safe). Fabricaciones estructurales §17 #20 intactas; core/autopilot.py SIN CAMBIOS
- validación: 22/22 PASS en tests/test_fact_checker.py, incluida reproducción del caso real book_65 en ambos escenarios. Peor caso 5×20s=100s < timeout 180s del módulo
- ampliación same-day: ERROR sin source_url y sin firma estructural degrada directo a WARNING, sin segunda pasada — evita autoconfirmación estable del LLM cuando no hay fuente que verificar. 24/24 PASS.
- Ampliación 2 same-day: presupuesto agregado de 120s para evitar timeout del scheduler con muchas claims verificables. 26/26 PASS.
- estado: FIXED + VALIDATED (§16 / §17 #25)

FASE refactor chapter_writer por idioma (3 fixes secuenciales) / 2026-08-25
- contexto: petición del usuario — evitar mezcla de idiomas en chapter_writer; preferencia por duplicar funciones antes que mantener ramas `if language` internas
- fix 1 (bug real): _build_prompt separado en _build_prompt_es/_build_prompt_en puras; BUG corregido: el esqueleto/pie de instrucciones estaba fijo en español incluso con language="en" — EN ahora tiene esqueleto íntegro propio. Wrapper delgado de dispatch preservado. Tests nuevos: test_build_prompt_en_excludes_es_skeleton, test_build_prompt_es_keeps_es_skeleton → 137 passed, 4 failed (preexistentes §17 #19), 1 skipped
- fix 2 (refactor puro, sin cambio de comportamiento): _elaborate_fact_deterministic, _elaborate_fact_pair_deterministic y _deterministic_section_paragraphs separadas en variantes _es/_en puras (section_paragraphs llama a fact_deterministic por idioma, no a los wrappers) → -k "deterministic" 6/6 PASS; regresión 95 passed, 4 failed preexistentes
- fix 3 (cierre): _build_section_continuation_prompt y _fallback_chapter separadas igual. Grep final: exactamente 6 `if language` restantes en main.py, todas dentro de los 6 wrappers de dispatch — cero ramas de idioma en lógica de construcción de texto
- validación final: pytest test_chapter_writer.py + test_chapter_writer_placeholder.py + test_runner_e2e_001.py → 161 passed, 4 failed, 1 skipped (234.42s); E2E runner 100% PASS. Los 4 fallos son los tests stale de §17 #19, confirmados sobre código prístino (git stash)
- archivos: modules/chapter_writer/main.py (PROTECTED, autorización concedida), tests/test_chapter_writer.py — estado: FIXED + VALIDATED

FASE borrado de libros (endpoint DELETE + botón front) / 2026-08-25
- funcionalidad nueva (no bug): endpoint DELETE /api/books/<id> en frontend/frontend_api.py — 200 si se borró, 404 si no existe, 409 si el job autopilot está PENDING/RUNNING; borra chapters+books en una transacción SIN tocar sources (compartidas vía chapter_ids/url_hash, anti-reciclaje intacto); borra DOCX best-effort (output/docx/book_{id}_*.docx, helper _book_docx_paths); TODO documentado para imágenes/checkpoints/jobs JSON (fuera de alcance autorizado)
- front: botón "🗑 BORRAR LIBRO" en app.js (autopilot-actions), oculto con job en ejecución, confirm() nativo en español con el título, actualización de state.books y re-render sin recargar
- incluye fix lateral de api_book_detail (.get() sobre sqlite3.Row → AttributeError), ver §16
- validación: 6 tests nuevos en tests/test_frontend_api_delete_book.py; pytest delete_book + autopilot + docx + frontend_api → 54/54 PASS; node --check app.js OK

FASE borrado masivo de libros / 2026-08-25
- qué se hizo: refactor del endpoint DELETE individual a un helper compartido _delete_book_core(book_id) en frontend/frontend_api.py (mismas reglas: 404/409 RUNNING/transacción chapters+books/sources intactas/DOCX best-effort). Nuevo endpoint DELETE /api/books (sin id): borra todos los libros, salta los que estén PENDING/RUNNING (no corta la operación), devuelve {deleted, skipped_running, total_books}; BD vacía → 200 con listas vacías. Cero lógica duplicada: individual y masivo solo invocan el helper.
- frontend: botón "🗑 BORRAR TODOS LOS LIBROS" en el centro de control (btn-danger), confirmación reforzada en dos pasos (confirm() + prompt() pidiendo escribir el número exacto de libros a borrar, avisando primero cuántos se saltarán por estar en ejecución); tras 200 quita solo los borrados de state.books (los skipped permanecen) y re-renderiza sin recargar
- archivos: frontend/frontend_api.py, frontend/index.html, frontend/app.js, tests/test_frontend_api_delete_book.py (3 tests nuevos: skip de running, BD vacía, sources intactas)
- validación: 9/9 PASS en tests/test_frontend_api_delete_book.py + node --check OK
- estado: FIXED + VALIDATED

FASE plan bilingüe completo (fix §17 #21) / 2026-08-25
- problema: en libros bilingües ("es,en") el plan editorial (título/descripción del libro, títulos de capítulo, headings de sección) se generaba una sola vez y solo en español; la edición EN heredaba TOC/headings/captions españoles y prompts de imagen con texto ES (evidencia: book_62_en.pdf, conservado intacto)
- solución (Opción A): columnas books.title_en/description_en + chapters.title_en/outline_en (migración idempotente en core/database.py); book_planner traduce TODO el plan con UNA llamada LLM extra (validación all-or-nothing: alineación índice a índice, cadenas no vacías, rechazo byte-idéntico al ES; fallback determinista mapea outline_en canónico sin red); timeout interno PLANNER_TRANSLATE_TIMEOUT=60s + module.json 120→160s; frontend/editorial.py selecciona _en cuando bilingüe+EN+no NULL (fallback ES explícito con log INFO); core/autopilot.py solo wiring aditivo de persistencia + alias de _resolve_book_languages (movida a editorial). document_builder/chapter_writer/image_planner/image_generator SIN CAMBIOS
- validación: 11 tests nuevos → 65/65 PASS (tests/test_book_planner.py + tests/test_editorial_bilingual_plan.py); regresión _resolve_book_languages 47/47 PASS (test_autopilot_research_sources/document_output/editorial/writer_en); humo offline real con payload exacto book_62 (BD temporal aislada, borrada al terminar); py_compile OK; book_62 intacto como evidencia
- estado: CERRADO - FIXED + VALIDATED (los 6 libros bilingües existentes 56-60/62 quedan con campos _en NULL por diseño, no se retro-traducen; regeneración manual de book_62 pendiente de decisión)

FASE cierre asimetría outline_en en camino de éxito del planner (amendment §17 #21) / 2026-08-25
- problema: en el camino de ÉXITO del plan principal LLM (no fallback), si la traducción fallaba validación (_validate_translation=False), title_en/description_en quedaban None PERO outline_en también quedaba None — a diferencia del camino _fallback_plan que sí aplicaba _deterministic_outline_en. Asimetría que perdía cabeceras de sección traducidas en el caso "mejor" (plan LLM OK) justamente cuando más riesgo de truncamiento (20 capítulos).
- solución (modules/book_planner/main.py:675): cuando valid_translation=False, outline_en se popula con _deterministic_outline_en(sections) (mapeo canónico Introducción→Introduction, Desarrollo→Development, Conclusión→Conclusion) como red de seguridad; title_en/description_en siguen None en ese caso (comportamiento honesto preservado).
- validación: 66/66 PASS (1 nuevo test + 65 anteriores en tests/test_book_planner.py); regresión _resolve_book_languages sigue 47/47 PASS (arquitectura intacta); humo offline reprodujo el camino (15/20 caps con señales ES; en fallback → outline_en recuperado canónicamente, title_en/description_en None)

FASE fallback research bilingüe (fix book_62) / 2026-08-25
- problema: en libros bilingües ("es,en"), si la pasada research del idioma SECUNDARIO ("en") fallaba por source_count < min_sources (query en español sin traducir → filtro de anclaje descarta los candidatos ingleses; evidencia book_62: 4 tasks "en" con source_count=1-2 < min=3), `_run_research_multilang` abortaba el job COMPLETO aunque la pasada "es" hubiera tenido fuentes de sobra
- solución: fallback focalizado en core/autopilot.py::_run_research_multilang — SOLO idioma no primario y SOLO gate de source_count insuficiente: sources_by_lang[secundario]=copia del primario, warning en log + métricas (`warnings`), per_language_status="FALLBACK", fase PASS. Fallo primario u otro tipo de fallo → hard fail sin cambio. job.data.sources sin tocar. modules/research/main.py SIN CAMBIOS
- validación: test_i_bilingual_secondary_lang_low_source_count_falls_back (nuevo) + 11/11 PASS en tests/test_autopilot_research_sources.py (test_g/test_h de regresión en verde); book_62 intacto como evidencia
- estado: CERRADO - FIXED + VALIDATED (el gap de traducción de query EN sigue abierto como §19 P3, ya mitigado a nivel de orquestación)

FASE mitigación fabricación histórica (book_59) / 2026-08-24
- problema: el pipeline podía fabricar hechos históricos falsos con apariencia factual y entregarlos en el DOCX sin bloqueo (evidencia: book_59 cap.2 — outline no anclado a fuentes, writer inventó Eichmann/campos 1942-1948/víctimas sin research, fact_checker WARNING + gate PASS)
- solución en 3 capas: (1) fact_checker — _has_fabrication_signature/_is_unsupported_issue/_escalate_fabrication_issue: claims con especificidad factual verificable (fecha+cifra+nombre propio o bigrama propio) sin soporte escalan a ERROR y quality_gate=FAIL; (2) chapter_writer — instrucción anti-invención ES/EN en _build_prompt (generalizar en vez de fabricar especificidad); (3) book_planner + schemas + editorial — BookPlanPayload.sources opcional, build_payload outline pasa fuentes reales resumidas por idioma, REGLA DE ANCLAJE A FUENTES en el prompt del outline (sin sources, prompt idéntico)
- validación: 12 tests nuevos (fact_checker/chapter_writer/book_planner/editorial), incluida la reproducción exacta del caso real book_59 en las 3 capas; checkpoint suite completa 685 passed, 4 failed (preexistentes §17 #19), 1 skipped, 131.49s — sin regresión
- limitaciones: capa writer es mitigación probabilística de prompt (la barrera dura es fact_checker); capa planner depende de que research produzca fuentes reales
- estado: CERRADO - FIXED + VALIDATED (book_59 intacto como evidencia histórica)

FASE validación research multi-idioma en producción (book_57 stale-process / book_58 confirmación) / 2026-08-24
- hallazgo 1 (stale resuelto): book_57 se sirvió con research en memoria anterior al fix multi-idioma pese al orquestador nuevo activo (mezcla de versiones; job 19:37–20:03 vs archivos 20:49–20:50) — 4ª ocurrencia del patrón stale-process (§19 P2). Resuelto con reinicio limpio (PID 20360, 21:33:32); book_57 intacto como evidencia histórica
- hallazgo 2 (anclaje funcionando): _has_anchor_keyword vigente rechaza manualmente las 4 fuentes ofensoras de book_57 (4/4 pasa=False) y la validación con book_58 ('Curiosidades sobre los pulpos', es,en, COMPLETED) no registró ninguna fuente fuera de tema — caso cerrado en §16
- hallazgo 3 (gap EN confirmado como deuda nueva §19 P3): pasada EN de book_58 devolvió 0/8 fuentes en dominios ingleses, idénticas a la pasada ES — query sin traducir/localizar; no bloqueante, pendiente de decisión de diseño
- estado: CERRADO - VALIDADO (stale + anclaje); deuda nueva abierta para el gap EN

FASE research multi-idioma (fix book_56, cierre deuda §19 P3) / 2026-08-24
- problema: en libros bilingües ("es,en") la fase research consultaba solo es.wikipedia.org (hosts hardcodeados) y entregaba las mismas fuentes ES a writer ES y writer EN; el DOCX EN mostraba hechos españoles literales dentro de plantillas inglesas (evidencia real book_56 'La historia del café', writers en execution_mode=deterministic por caída de Ollama)
- solución: capa fetch de modules/research/main.py parametrizada por language (_wiki_search/_wiki_extract/_wiki_rest_summary/_backend_wikipedia/_backend_wikidata; hosts es/en.wikipedia.org); core/autopilot.py::_run_research_multilang ejecuta research UNA vez por idioma en libros bilingües (fuentes fusionadas+deduplicadas en job.data.sources, desglose en job.data.sources_by_lang); frontend/editorial.py pasa el idioma al payload de research y selecciona sources_by_lang[idioma] por writer. _deterministic_curate() sin cambios (ranker puro; cubre ruta LLM y backstop determinista)
- validación: test_g_bilingual_book_runs_research_once_per_language + test_h_monolingual_es_book_still_single_research_call (nuevos, tests/test_autopilot_research_sources.py) + 8/8 PASS en ese archivo + 28/28 PASS previo (test_research_multisource/sources/curation)
- estado: CERRADO - VALIDADO

FASE reconciliación §17 #19 / 2026-08-24
- fix: fila de tests obsoletos de chapter_writer movida de §16 (donde estaba incorrectamente como OPEN dentro de "resueltos") a §17 como fila numerada #19, resolviendo las referencias cruzadas rotas en §23.
- estado: CERRADO - solo documentación, sin cambios de código.

FASE diagnóstico generación EN / 2026-08-24
- objetivo: determinar si el pipeline real invoca alguna vez la rama de generación en inglés (tarea 6 §20)
- método: solo lectura — grep + inspección de core/autopilot.py, core/schemas.py, frontend/index.html, frontend/app.js, frontend/editorial.py
- hallazgo: NINGÚN wiring real. write_chapter_en y translate_es_en/en_es tienen schema/módulo pero están huérfanos del pipeline; frontend no tiene selector de idioma; books.languages siempre default "es"
- estado: DIAGNOSED — decisión de diseño pendiente del arquitecto (ver §20 tarea 6)

FASE limpieza sources_fallback / 2026-08-24
- problema: clave `sources_fallback` en _UI_STRINGS (modules/document_builder/main.py, bloques es/en) definida pero sin consumidor real — código muerto detectado durante diagnóstico de §17 #9/#13/#14
- solución: eliminada la clave de ambos bloques de idioma (es/en); confirmado por grep repo-wide (tests/, modules/, core/, frontend/) que no tenía ningún consumidor
- validación: modules/document_builder/tests/test_document_builder.py 30/30 PASS + py_compile OK
- estado: CERRADO - VALIDADO (sin impacto funcional; la omisión de la sección de fuentes cuando no hay fuentes ya dependía de `if real_sources:`, no de esta clave)

FASE cierre backlog cosmético #11/#12/#18 / 2026-08-24
- #12: quality_control reconoce ahora metadata en convención {image_id}.metadata.json (afectaba a image_generator e image_search, no solo este último como se creía)
- #18: document_builder convierte .webp a PNG en memoria antes de insertar (Pillow), con fallback seguro si falla
- #11: image_search filtra por relevancia temática reutilizando _has_anchor_keyword de research (topic opcional, retrocompatible), payload propagado desde autopilot
- validación: 9+26+9 tests focalizados PASS (quality_control, document_builder, image_search respectivamente)
- estado: CERRADO - VALIDADO los 3

FASE fix títulos de capítulo (TOC duplicado) / 2026-08-24
- problema: book_planner._fallback_plan volcaba la idea completa del usuario como título de cada capítulo; document_builder._add_toc duplicaba el prefijo "Capítulo N:" sobre títulos que ya lo traían
- solución: _short_idea_title() en book_planner (8 palabras + "Parte N"); regex defensiva en _add_toc (document_builder); reparación puntual de los 24 chapters.title de book_55 en BD de producción (backup previo)
- validación: 74+25 tests focalizados PASS; CONFIRMADO VISUALMENTE con regeneración real del DOCX de book_55 (TOC legible, sin duplicación)
- estado: CERRADO - VALIDADO (deuda menor: títulos de fallback siguen siendo genéricos, no temáticamente distintos — ver §19)

FASE fix sección de fuentes DOCX / 2026-08-23
- problema: document_builder renderizaba literalmente la cola "## Fuentes" escrita por el LLM del writer, causando duplicación (#9), fabricación de citas APA falsas (#13) y omisión (#14) — causa raíz: nunca usaba chapters.sources real
- solución: _split_sources_tail descarta siempre la cola del LLM; nueva sección determinista construida desde chapter.sources (URLs reales, hipervínculo); sección omitida si no hay fuentes
- validación: 22/22 PASS focalizado en modules/document_builder/tests/test_document_builder.py
- estado: CERRADO - VALIDADO (mitigación de producto; causa de fondo en writer queda como deuda no bloqueante, ver §17 #13)

FASE reconciliación wiring image_search / 2026-08-23
- qué se hizo: verificación de solo lectura (código estático + evidencia empírica) del wiring search+generate vía image_search_ratio; NO es un fix de código, es el registro como cerrado de un estado ya implementado en sesiones previas que había quedado documentado como BLOCKED por error.
- hallazgos: selector UI real en index.html (radios 0.0–1.0) → POST /api/books → books.image_search_ratio → _execute_per_chapter enruta image_gen a _run_image_gen_split (core/autopilot.py:1121), que con ratio>0 ejecuta search_chapter_images + generate_chapter_images en-proceso (_sched._process_task) con dedupe por image_path; ImageSearchPayload registrada en PAYLOAD_SCHEMAS y OUTPUT_SCHEMAS["search_chapter_images"]=ImageGenerateOutput (el supuesto bloqueo de schema nunca existió).
- evidencia producción: book_44 (generado desde frontend 2026-08-23) con imágenes searxng/bing reales mezcladas con generadas.
- estado: IMPLEMENTED + VALIDATED + CONFIRMADO OPERATIVO EN PRODUCCIÓN (ver §16 y §23). Siguiente frente: §17 #5 (denylist de dominios).

FASE confirmación producción fix anti-reciclaje / 2026-08-23
- problema: el fix anti-reciclaje (ver FASE 2026-08-22 más abajo) estaba validado unitariamente y con colisión natural (book_42), pero faltaba ejercicio directo y controlado de la rama de rechazo contra datos reales.
- solución: script puntual tools/dev/archive/verify_anti_recycling_prod.py — invoca run_job() REAL de core/autopilot.py sobre copia temporal de la BD (SPACE_LAIR_DB_PATH) y BookJobStore aislado, con la fuente id 640 (Latveria) propuesta deliberadamente para book_id=45; sin tocar código de producción ni la BD real.
- validación: WARNING emitido textualmente ('Fuente 640 (Latveria) NO asociada a book 45: sin anclaje temático (_has_anchor_keyword=False, libros previos [40])'); sources.id=640.chapter_ids sin cambios ([176,177,178]); book_45 solo con sus 8 fuentes legítimas (663-670); job status=COMPLETED.
- estado: FIXED + VALIDATED + CONFIRMADO EN PRODUCCIÓN (2026-08-23). Cierre definitivo de §17 #6.


FASE fix fact_checker dedupe claims / 2026-08-22
- problema: fact_checker inflaba claims_checked cuando el LLM repetía el mismo claim dentro de una única salida JSON — book_39 cap 173 mostró 14 claims = 7 únicas × 2; descartada acumulación entre reintentos (phase["metrics"] se reemplaza, no suma).
- solución: dedupe por texto de claim normalizado (lowercase+strip+espacios colapsados, set seen_claims, conserva primera aparición) en el bucle de normalización de execute() en modules/fact_checker/main.py (OUT_OF_SCOPE, autorizado puntualmente); claims_checked hereda el conteo ya deduplicado.
- validación: test_execute_dedupes_repeated_claims (nuevo) + 14/14 PASS en tests/test_fact_checker.py.
- estado: FIXED + VALIDATED (forward-only; NO corrige conteos ya persistidos — book_39 cap 173 requiere re-ejecutar fact_check).

FASE fix anti-reciclaje de fuentes / 2026-08-22
- problema: SourceManager.add_source (dedupe por url_hash) re-asociaba fuentes ya persistidas para otros libros sin re-validar anclaje temático — book_39 (Doom) heredó 'Latveria' (Marvel, insertada 2026-08-16 para book_23).
- solución: get_source_by_url + book_ids_for_source en core/book/source_manager.py; en core/autopilot.py, si la fuente existe y pertenece a otro book_id, se re-valida con _has_anchor_keyword (import de modules/research/main.py, sin modificarlo) contra topic/título del libro nuevo; si falla, WARNING y no se asocia.
- validación: test_stale_source_not_reassociated_without_topic_anchor (nuevo) + 46/46 PASS focalizados. Servidor reiniciado a PID 25516 (23:36:30, posterior a ambos archivos tocados) con verificación explícita de timestamps.
- evidencia producción: book_42 (topic idéntico a book_40 → colisión natural por url_hash) — control positivo/negativo: 6 fuentes Doom legítimas SÍ reasociadas al capítulo nuevo; 2 fuentes Marvel stale (Latveria sources.id=640, Pantera Negra sources.id=641) devueltas por la búsqueda pero NO reasociadas (chapter_ids intactos [176,177,178], ausentes de chapters.sources del cap.180).
- estado: FIXED + VALIDATED + CONFIRMADO EN PRODUCCIÓN REAL.


FASE confirmación fix quality_control / 2026-08-22 (cierre)
- que se hizo: reinicio limpio del servidor (PID 30336 stale → PID 11200, 23:12:05, posterior a los 4 módulos tocados) + retry de book_38 (POST /api/books/38/autopilot/retry).
- resultado end-to-end: quality_gate check de imágenes "5 imágenes por capítulo" (PASS, antes '!=3'); job status=COMPLETED con las 10 fases PASS; output/docx/book_38_es.docx generado (7.505.053 bytes, 23:12:35). QC overall WARNING solo por imágenes sin metadata (no bloqueante).
- estado: CONFIRMADO EN PRODUCCIÓN REAL — ver §16 y §19 P2 (stale-process).


FASE fix quality_control image_count / 2026-08-22
- problema: quality_gate._check_images comparaba contra literal hardcodeado 3 en vez de books.image_count real — cualquier libro con image_count≠3 fallaba el gate de imágenes aunque el pipeline hubiera entregado exactamente las imágenes pedidas (evidencia real: libro 38, image_count=5, 5 imágenes/capítulo reales, QC FAIL "Imágenes por capítulo != 3").
- solución: expected = clamp(book.image_count or 3, 0, 20) en modules/quality_control/main.py (_check_images); comparación y mensajes usan expected en vez del literal 3. Test nuevo test_check_images_uses_book_image_count_not_literal.
- validación: modules/quality_control/tests/ 8/8 PASS + tests/test_quality_gates.py 14/15 PASS (1 fallo preexistente y ajeno, ver §19 P2).
- estado: FIXED + VALIDATED.


FASE fix metrics gate / 2026-08-22
- problema: phase['metrics'] quedaba '{}' cuando una fase con gate real (quality_gate/fact_check/research) fallaba — el desglose del módulo (overall_status, book_checks, etc.) se perdía al persistir el job, dejando el FAIL sin diagnóstico posible (evidencia real: libro 32, quality_gate FAIL con metrics={}).
- solución: en run_job (core/autopilot.py), rama else (result.ok=False), añadida phase['metrics'] = result.metrics or {} antes de la bifurcación por attempts — mismo patrón que la rama de éxito, aplicado a PHASE_RETRY y PHASE_FAIL.
- validación: test_quality_gate_fail_persists_real_metrics (nuevo) + regresión focalizada: test_autopilot_quality_gate_payload.py 2/2 PASS, test_autopilot_fact_check_gate.py 2/2 PASS. Suite completa no ejecutada (cambio focal).
- estado: CERRADO - VALIDADO

FASE 8N.3 / 2026-08-18
```text
FASE 8N.3 / 2026-08-18
- que se hizo: expuesto image_search_ratio en el front (radio-group en index.html, lectura/envío en app.js, persistencia clamped en editorial.py::create_book()). Cierra el ciclo completo de la feature iniciada en 8M.2/8N.2 (P1 schema ya cerrado).
- hallazgo colateral: se detectó y documentó retroactivamente un cambio no confirmado en build_payload() (visual_style/genre, ver fila nueva en §16) presente en el working tree desde antes de esta sesión, sin FASE propia previa.
- validación: test_create_book_persists_image_search_ratio 1/1 PASS + test_editorial_panel.py 24/24 + test_editorial_metadata.py 6/6 + node --check OK
- estado: CERRADO - VALIDADO

FASE 8N.2 / 2026-08-18
- que se hizo: cierre de P1 §19 — registro de `ImageSearchPayload(TaskPayload)` en core/schemas.py `PAYLOAD_SCHEMAS` para capability `search_chapter_images`; los 6 campos (book_id, chapter_number, language, chapter_title, chapter_text, num_images) coinciden con los leídos por modules/image_search/main.py. `OUTPUT_SCHEMAS` reutiliza `ImageGenerateOutput` existente (plug-compatible, sin duplicar clase).
- validación: test_schemas_image_search.py 3/3 PASS (0.17s) + regresión focalizada test_autopilot_persist_chapters.py 4 passed in 1.84s + `python -c "from core.module_registry import *; import core.schemas; print('OK')"` → OK.
- estado: **CLOSED** — P1 §19 cerrada; images_per_chapter con ratio>0 ya no falla en validate_payload (a nivel de schema; aún sin exponer en el front).

FASE image_search (módulo nuevo standalone) / 2026-08-17
- que se hizo: módulo nuevo modules/image_search/ (capability search_chapter_images): búsqueda de imágenes vía SearXNG (GET /search?categories=images&format=json, sin LLM), con timeouts cortos (search ~15s / descarga ~10s) y fallback resiliente (si SearXNG no responde o una descarga falla NO lanza excepción: devuelve las imágenes obtenidas y el resto en status=error). Persiste en el MISMO patrón de ruta que image_generator (data/images/books/{book_id}/chapters/{chapter_number}/images/) con un *.metadata.json por imagen. Shape de retorno plug-compatible con generate_image (superset de campos: source_type, source_url, engine, resolution, license=None).
- validación: 5/5 tests PASS (modules/image_search/tests/), HTTP mockeado — no depende de un servidor SearXNG real. Suite completa NO ejecutada (tarea aislada; no se tocaron autopilot/editorial/database/frontend).
- estado: IMPLEMENTED, NOT_YET_WIRED (no está conectado a autopilot/editorial; próxima fase: columna image_search_ratio en books + split en build_payload)

FASE 8N.1 / 2026-08-17
- problema: regresión del estilo visual por defecto en `image_planner` — tras quitarse `or "realistic"` en `frontend/editorial.py` en una sesión previa, `_build_fallback_plan` y `_normalize_images` seguían usando el string largo `"Fotografía editorial, paleta coherente, detalle realista"` como fallback, en vez de `"realistic"`.
- solución: 2 puntos corregidos en `modules/image_planner/main.py` (l.204 `_build_fallback_plan`, l.365 `_normalize_images`) → default `"realistic"`; test `test_no_genre_keeps_default_style` actualizado para reflejar el default real corregido.
- validación: 23/23 PASS (`tests/test_image_planner.py`, collect-only confirmado). Grep repo-wide (`*.py`,`*.js`,`*.md`) del string antiguo: 2 coincidencias residuales — `image_planner/main.py:233` (`_build_prompt`, texto de guía LLM; ruta de fallback sin LLM ya usa `"realistic"`) y `tools/gen_book30_fallback_image.py:29` (script auxiliar, fuera de alcance). Registrado como deuda menor P3 en §19.
- estado: CERRADO - VALIDADO (fix confirmado con diff literal + grep + recuento de tests; línea 233 documentada como deuda cosmética, no bloqueante)

2026-08-17 / FRENTE COMFYUI COMPLETO (provider reescrito -> timeout externo -> guard de presupuesto -> connect timeout -> fix register_defaults -> flip)
- Provider ComfyUI reescrito (SDXL Base+Refiner real, 2 pasadas) con fallback a local; timeout externo de fase 360s (module.json); guard de presupuesto interno (IMAGE_TOTAL_TIME_BUDGET=330s, margen 90s); COMFYUI_CONNECT_TIMEOUT corto (10s) para el POST de encolado (fallback_reason=comfyui_unreachable); fix de registry._register_defaults (mover default=True a ComfyUiProvider — el cambio de la constante DEFAULT_PROVIDER solo no bastaba, get() resuelve self._default antes) y flip: DEFAULT_PROVIDER="comfyui" ACTIVADO POR DEFECTO desde 2026-08-17. Validado con servidor real vía resolución por default (get(None) → Clase ComfyUiProvider, imagen real, 95.4s); fallback sin excepción; 12 PASS + 6/6 image_generator. Ver §16/§8.

2026-08-17 / GUARD DE PRESUPUESTO IMAGE_GENERATOR (cierre §19/a)
- Guard de presupuesto total en `image_generator` (`IMAGE_TOTAL_TIME_BUDGET=330s`) cierra el
  encaje entre `COMFYUI_POLL_MAX_WAIT=300s` por imagen y `timeout_seconds=360s` del scheduler —
  evita que un preset de 3 imágenes/capítulo pierda el commit completo de la fase si la primera
  imagen tarda cerca de su límite; fallback limpio por imagen en su lugar. 6/6 PASS módulo
  image_generator. Proveedor ComfyUI real queda IMPLEMENTED + VALIDATED end-to-end,
  DEFAULT_PROVIDER sin tocar.

2026-08-17 / COMFYUI REAL IMAGE PROVIDER (diseño + implementación)
- `core/image_providers/comfyui.py` reescrito de cero: workflow SDXL Base+Refiner en dos
  pasadas reconstruido a partir de los nodos especificados (4,5,6,7,10,11,12,15,16,17,19) —
  el JSON exacto llegó vacío; sustituciones por imagen (prompt/negativo en base 6/7 y refiner
  15/16, width/height en 5 por aspect_ratio, noise_seed aleatorio en 10, checkpoints 4/12 vía
  env, filename_prefix en 19 con seed). Helpers `http_bytes`/`_env_float` en
  `core/image_providers/base.py`.
- validación aislada real (script, no pytest) contra servidor ComfyUI (0.33.1): imagen real
  generada (189k colores únicos, no placeholder), ratios 1:1 y 16:9 confirmados, ~72–80
  s/imagen a 25 steps (Base+Refiner); fallback automático a LocalImageProvider validado sin
  excepción (COMFYUI_URL=127.0.0.1:9999 → metadata fallback=True).
- tests existentes 20+6 PASS sin regresión. DEFAULT_PROVIDER en registry.py NO tocado
  (pendiente: check de timeout de fase image_gen, 180s).

CHECKPOINT 8Z.2 / 2026-08-16
- (1) Anchor de relevancia en research: nueva `_has_anchor_keyword(topic, cand)` — filtro compuesto (`_keyword_overlap >= 0.15 AND _has_anchor_keyword`) que ancla la relevancia al tema del libro (topic, ya presente en el payload). `topic` opcional en `research_web()`, retrocompatible; `topic=None/""` no bloquea. 25/25 PASS (research_sources/multisource/curation). Ver §16.
- (2) Gate espurio en fact_check: `autopilot._run_single` pasó el bloque fact_check de `if st=='FAIL' or qg=='FAIL'` a `if qg=='FAIL'`, eliminando fallos de fase espurios con `quality_gate=PASS` (books 9,18,19,23,25). Bloque research intacto (8H.3). 32/32 PASS (test_autopilot_fact_check_gate/editorial/quality_gate_payload/document_output). Ver §16.

FASE 8M.2 / 2026-08-16
- objetivo: integrar SearXNG (infraestructura 8M.1) como candidato más en el pool multi-fuente de `research` y evitar starvation bajo el default de producción (max_sources=5).
- solución (a): `modules/research/main.py` — `_search_searxng` (HTTP a http://localhost:8081, parseo tolerante a JSON/fenced + fallback a [] en error/timeout) integrado en `_multi_source_search`; orden de backends: Wikipedia es/en → Wikidata → SearXNG → archive.org opcional (RESEARCH_ARCHIVE_ENABLED).
- solución (b): `SOURCE_PRIORITY["web_searxng"] = 2` (mismo nivel que web_wikidata=2, > web_archiveorg=1, < web_wikipedia=3). Antes la clave ausente implicaba prioridad 0; el 2 refleja la calidad de SearXNG como agregador sin despulsar a Wikipedia (prioridad máxima).
- solución (c) [FASE 8M.2-fix, causa raíz]: la truncatura POR ORDEN DE LLEGADA en `_multi_source_search` se reemplazó por un corte SOLO tras dedupe + ranking determinista (`_deterministic_curate`, priority+overlap). Antes, `len(candidates) >= max_sources -> break` dentro del bucle de dedupe descartaba candidatos de backends posteriores (p.ej. SearXNG) cuando Wikipedia saturaba los huecos; ahora cada backend aporta hasta `per_backend_limit` y el corte decisivo respeta prioridad/overlap (nunca el orden de llegada). try/except por-backend y timeouts se mantienen.
- validación unitaria: `pytest tests/test_research*.py` -> 23 passed, 0 failed, 0 errors (1.35s). Ningún test requirió ajuste: los tests asercionan conteos/caps/dedupe y casos aislados por stubs (no el orden de llegada); el contrato `len <= max_sources` y `per_backend_limit` se conservan. Fixture autouse stubbea SearXNG a [] en multisource/sources; el backend real contra contenedor se valida en tests/test_research_searxng.py.
- prueba de efectividad del fix (demo controlada): max_sources=2, Wikipedia (overlap=0) vs SearXNG (overlap=1.0 sobre "sistema solar"); OLD (arrival-order truncation) devolvía ['web_wikipedia','web_wikipedia']; NEW (priority+overlap) devuelve ['web_searxng','web_searxng'] ⇒ prioridad/overlap decide, no el orden de llegada.
- HUMO REAL (max_sources=5, default de producción; RESEARCH_USE_LLM=0; Wikipedia/Wikidata/SearXNG en vivo contra contenedor localhost:8081, HTTP 200): `_search_searxng("sistema solar",5,20)` directo -> 5 candidatos reales y diversos (es.wikipedia.org, ucm.es, youtube.com, nasa.gov, nationalgeographic.com.es) => infra OK. `research_web("sistema solar", max_sources=5)` -> total=5, web_searxng=0, dominios=['es.wikipedia.org']. Con max_sources=30: `_multi_source_search` -> 30 (15 wikipedia + 15 searxng), dominios reales diversos (nasa.gov, esa.int, iac.es, ucm.es, twinkl.es, youtube.com, www.esa.int, ...).
- NOTA / hallazgo abierto (no es bug de código): el fix de truncatura CORTA (priority decide; demo arriba), PERO bajo el default max_sources=5 el humo "sistema solar" sigue 0 web_searxng porque Wikipedia devuelve 5 artículos con prioridad 3 y keyword overlap ~1.0 que legítimamente ocupan el top-5 del ranking determinista. Para queries de alta cobertura de Wikipedia, searxng solo aparece si max_sources lo permite (>=6) o si su overlap supera al de Wikipedia. Palancas abiertas: (a) elevar el default de max_sources (p.ej. 8) en core/schemas.py + frontend/editorial.py, o (b) rebalancear prioridades — no aplicadas en este cierre (fuera del scope autorizado).
- estado: CORTÉ DE TRUNCATURA CERRADO (verified, tests PASS, demo PASS); GATE DE HUMO (searxng>0 bajo max_sources=5) NO ALCANZADO para "sistema solar" por dominio de Wikipedia — requiere decisión sobre el default de max_sources (ver hallazgo abierto). Infra SearXNG verificada funcional (HTTP 200, candidatos reales).

FASE 8M.2-fix / 2026-08-16
- objetivo: elevar el default de max_sources de 5->8 para resolver el GATE DE HUMO pendiente (searxng=0 bajo max_sources=5 para "sistema solar") y dar espacio a la diversidad de fuentes sin romper prioridad de Wikipedia.
- cambio (default 5->8, todos "duros" sin fallback al schema pydantic):
  - core/schemas.py:190 -> `Field(default=8, ge=1, le=20)` (mantenido ge=1, le=20)
  - frontend/editorial.py:461 -> `int(data.get("max_sources") or 8)` (or 5 -> or 8)
  - modules/research/main.py:586 -> `research_web(..., max_sources: int = 8, ...)` (signature)
  - modules/research/main.py:743 -> `int(payload.get("max_sources", 8))` (validate_payload)
  - modules/research/main.py:764 -> `validated.get("max_sources", 8)` (execute, safety net)
  - Caso research/main.py: los 3 defaults eran "duros" (hard-coded a 5, sin fallback al schema). Ajustados a 8 para consistencia.
- motivo: con max_sources=5, Wikipedia (prioridad 3, overlap ~1.0) saturaba el top-5 y SearXNG quedaba fuera (hallazgo abierto sec. 823). Elevar a 8 da espacio a la diversidad sin despulsar la prioridad maxima de Wikipedia (prioridad 3 > SearXNG=2).
- investigacion step 2 (módulos downstream): Select-String en modules/fact_checker/*.py, modules/editor/*.py, modules/quality_control/*.py con patron `max_sources|source.*5|5.*source` -> NO MATCHES. Ningun modulo downstream asume o valida un maximo/minimo de fuentes atado al 5. El gate de min_sources (default 3) no depende de max_sources; el gate usa `len(stored) >= 1`.
- estimacion timeout (4 backends, peor caso todos fallan): Wikipedia(_wiki_search _request timeout=20)=20s + Wikidata(_request timeout=20)=20s + SearXNG(_request timeout=20, llamado con timeout=20)=20s + Archive(_request timeout=20)=20s. Total: 4x20=80s < RESEARCH_TOTAL_TIME_BUDGET=90 y < timeout_seconds=160 del module.json (subido 120->160; margen 40s; holgado). Con RESEARCH_USE_LLM="1" (default): +RESEARCH_PROVIDER_TIMEOUT=40 -> 80+40=120s < 160s (margen 40s, holgado). En el humo (RESEARCH_USE_LLM="0"): 80s, holgado bajo 160s. Nota: max_sources=8 no incrementa el numero de requests HTTP por backend en el escenario "todos fallan" (1 search request que falla -> 0 extracts); solo incrementa per_backend_limit en _multi_source_search.
- validacion unitaria: `pytest tests/test_research*.py -v` -> 23 passed, 0 failed, 0 errors (1.70s). Ningun test requirio ajuste; los tests usan max_sources explicitos (no el default de la funcion).
- HUMO REAL (max_sources=8, RESEARCH_USE_LLM=0, Wikipedia/Wikidata/SearXNG en vivo contra contenedor localhost:8081, HTTP 200): `research_web("sistema solar", max_sources=8)` -> total=8, web_searxng=1, dominios=['es.wikipedia.org', 'ecologiaverde.elperiodico.com']. Antes (max_sources=5): total=5, web_searxng=0, dominios=['es.wikipedia.org'].
- estado: CERRADO — GATE DE HUMO ALCANZADO (web_searxng=1 > 0 bajo max_sources=8). CORTÉ DE TRUNCATURA (solucion (c) del 8M.2) sigue CERRADO. FASE 8M.2 comun finalizada.
- ajuste timeout (FASE 8M.2-fix-cont): con RESEARCH_USE_LLM="1" (default real), peor caso = 80s (4 backends x 20s) + 40s (RESEARCH_PROVIDER_TIMEOUT) = 120s = timeout_seconds original -> margen CERO. Eleccion (b): subir timeout_seconds 120->160 en modules/research/module.json (1 linea JSON, no altera logia de negocio; solo kill-timer del scheduler). Recalculo: 120s < 160s -> margen real de 40s (33%). Comentario actualizado modules/research/main.py:54-58 (max_sources=5 -> 8 en docstring de SOURCE_PRIORITY).


FASE 8M.1 / 2026-08-16
- objetivo: infraestructura SearXNG como backend de descubrimiento web amplio para research (complementa Wikipedia/Wikidata/archive.org)
- solución: infra/searxng/settings.yml + docker-compose.yml (imagen oficial searxng/searxng:latest, puerto 8081:8080, search.formats=[html,json,rss], restart=unless-stopped, contenedor aislado `searxng-test`)
- CORRECCIÓN de diagnóstico previo (misma sesión): el 403 inicial NO era por falta de cabecera X-Forwarded-For (eso es solo un log informativo de ProxyFix, cosmético, no bloquea); era por format "json" ausente en search.formats del settings.yml (webapp.py aborta 403 si el formato no está habilitado). No hace falta trusted_proxies ni cabeceras especiales en el backend Python.
- validación: curl sin cabeceras especiales -> HTTP 200, 27 resultados reales para "Isaac Newton" (Wikipedia en+es entre ellos), ~0.9s
- estado: CERRADO - INFRAESTRUCTURA LISTA PARA CONSUMO
FASE 8L.2 / 2026-08-16
- problema: continuación de 8L.1 (timeout 180s insuficiente con backstop forzado)
- solución: timeout_seconds 180->300 en modules/chapter_writer/module.json + CHAP_FORCE_MIN=1 reaplicado en run.py::web() (definitivo, no revertido)
- validación real (book_22, servidor real): COMPLETED, final_word_count=1620 (>=1500), quality_gate=PASS, final_quality_gate=PASS, deterministic_used=true, writer duration=151.8s (margen ~148s bajo el nuevo techo de 300s)
- observación (no bloqueante): 5/6 intentos de continuación rechazados por duplicación en esta corrida (successful=1, rejected=5); resultado final correcto pese a la tasa de descarte alta; vigilar si se repite
- estado: CERRADO - VALIDADO EN SERVIDOR REAL

FASE 8L.1 / 2026-08-16
- problema: capítulo bajo mínimo de palabras en servidor real (981<1500) porque CHAP_FORCE_MIN nunca se fijaba fuera del runner E2E (ver 8K.3/7.9D.7)
- intento 1: os.environ.setdefault("CHAP_FORCE_MIN","1") en run.py::web() -> REVERTIDO. Resultado: job FAILED (writer#79, timeout 180s), peor que el estado anterior (COMPLETED con 981 palabras). Mismo patrón que book_16.json (writer#59).
- causa raíz identificada: timeout de tarea de 180s en fase writer es insuficiente cuando el backstop determinista debe expandir el capítulo.
- estado: BLOQUEADO, pendiente investigar timeout (ver PASO B de esta sesión)
- research (8K.3) confirmado intacto en esta prueba: 5 fuentes reales PASS

FASE 8K.3 / 2026-08-16
- problema: falso positivo en el gate de relevancia de research — `_keyword_overlap` usaba substring (`w in haystack`) y no filtraba stopwords ES/EN de las keywords de la query. Con la query real "Los Dooms: El Último", keywords efectivas `['los','dooms','el','último']` y `'el'` coincidía por substring dentro de "... por el censo." → overlap=0.250 ≥ umbral 0.15. Las 3 fuentes irrelevantes reales del libro #18 (Crozet Virginia, Crimora Virginia, Sam Porter Bridges) PASABAN el filtro (bug) y se persistían.
- solución: (1) haystack tokenizado en set de palabras reales (`re.findall` + membership → coincidencia por palabra completa, no substring); (2) nueva `_STOPWORDS_ES` con lista mínima ES + EN común aplicada a las keywords candidatas extraídas de la query; (3) keywords efectivas = palabras ≥2 chars excluyendo stopwords; (4) +1 test `test_real_query_los_dooms_stopwords_filtro` en `tests/test_research_sources.py` con la query real y los candidatos irrelevantes reales del libro #18.
- archivos: `modules/research/main.py` (_keyword_overlap + _STOPWORDS_ES), `tests/test_research_sources.py` (nuevo, untracked)
- validación: evidencia real libro #18 — las 3 fuentes (Crozet/Crimora/Sam Porter Bridges) pasaron de overlap=0.250 (PASABAN, bug) a 0.000 (correctamente descartadas). Suite completa: 601 passed, 0 failed, 0 errors (142.61s). Diagnóstico libro #19: FAIL legítimo (0 candidatos en backends, no culpa del filtro).
- ESTADO: CERRADO - VALIDADO (research real, sin falsos positivos, fact_check PASS). El fallo de word_count (981<1500) observado en la prueba de aceptación NO es parte de este fix; es el gap ya conocido de CHAP_FORCE_MIN (ver punto 2).

FASE 8K.1 / 2026-08-15
- problema: outline.sections vacío en book_planner — el prompt del LLM no solicitaba el campo `sections` por capítulo y no existía fallback determinista (a diferencia de writer/editor); el outline con `sections=None`/`[]` provocaba NO_TARGET_SECTION en el writer y falla del mínimo de palabras
- solución: (1) `_build_prompt` exige `sections` (heading + objective) por capítulo; (2) `_DEFAULT_SECTION_HEADINGS`, `_default_sections()` y `_ensure_sections()` proveen fallback determinista para capítulos con sections ausentes/vacíos; (3) `_normalize_plan` aplica `_ensure_sections` a cada capítulo; (4) 3 tests de regresión nuevos en `tests/test_book_planner.py`
- archivos: `modules/book_planner/main.py`, `tests/test_book_planner.py` (único módulo tocado; chapter_writer/editor/research fact_checker quality_control PROTECTED u OUT_OF_SCOPE)
- validación: 49/49 PASS en test_book_planner.py; suite completa 596 passed, 0 failed, 0 errors (208.70s) — sin regresiones

FASE 8J.2 / 2026-08-15
- acción: checkpoint de integración — suite completa re-ejecutada tras 8I.1/8J.1
- resultado: 593 passed, 0 failed, 0 errors (105.56s) — sin regresiones; sin cambios de código en este checkpoint
- validación: pytest tests/ modules/ -q (suite completa, incl. runner E2E)

FASE 8J.1 / 2026-08-15
- problema: `book_planner.execute()` no emitía `language`/`genre` en su salida; en la ruta autómata real, `create_book` INSERTa NULL → `author`/`genre`/`language` ausentes del libro
- solución: `execute()` propaga `language` del payload (default "es") y `genre` inferido keyword-based determinista desde la `idea` (None si no hay keyword claro); `_fallback_plan` también emitidos; `author` intencionalmente omitido (no hay dato real del que derivarlo — no se inventa)
- validación: tests/test_book_planner.py + tests/test_editorial_metadata.py + tests/test_autopilot_quality_gate_payload.py — 53/53 PASS

FASE 8H.3 / 2026-08-15
- problema: fase research nunca fallaba aunque su resultado real fuera FAIL (gap de orquestación, detectado en prueba real con book 17 "Doom Dark Ages")
- solución: core/autopilot.py::_run_single traduce research al mismo patrón de gate_fail que quality_gate/fact_check
- validación: 58/58 tests focalizados PASS

FASE 8I.1 / 2026-08-15
- problema: research solo cubría Wikipedia ES, insuficiente para temas sin cobertura, forzando al writer a alucinar sin fuentes
- solución: búsqueda multi-fuente (Wikipedia es→en + Wikidata) + curación LLM opcional con patrón de seguridad idéntico a writer/editor (timeout, budget, fallback determinista) + validación anti-alucinación de URLs; archive.org implementado pero deshabilitado por defecto; DDG/GDELT evaluados y descartados
- archivos: modules/research/main.py (autorización explícita, OUT_OF_SCOPE) + 3 tests nuevos
- validación: 22/22 tests específicos PASS + 37/37 con quality_gates

FASE 8H.2 / 2026-08-15
- problema: deuda P3 "código duplicado/inaccesible en frontend_api.py"
- solución: microdiagnóstico de solo lectura confirmó que NO había código inaccesible (verificado con grep repo-wide de las 31 rutas y funciones a nivel módulo); se aplicaron 5 limpiezas de bajo riesgo: import Any sin uso, posixpath muerto, import os local redundante, import load_book duplicado, comentario "Workflow Endpoints" repetido + indentación
- archivos: frontend/frontend_api.py (único archivo tocado); se eliminó también archivo temporal huérfano tools/_fix_indent.py
- validación: ast.parse OK + pytest tests/test_frontend_api.py + test_frontend_api_autopilot.py + test_frontend_api_docx.py -q → 48 passed, 0 failed, 0 errors (10.48s)
- nota: NO se tocó el patrón approve/reject ni cancel/retry (riesgo medio, ver §19 mejora opcional); NO se ejecutó la suite completa

FASE 8H.1 / 2026-08-15
- problema: textos de interfaz en inglés (deuda P2 §19) + inconsistencia 424-428 (enum crudo en vez de PHASE_STATUS_LABEL)
- solución: traducidas 10 labels de fases, banner 'BOOK READY', 3 métricas de detalle de libro, live meta header; 424-428 migrado a PHASE_STATUS_LABEL; card de libro listo (línea 483) alineada con label de pipeline
- archivos: frontend/app.js (único archivo tocado)
- validación: node --check OK + grep de ausencia de cadenas originales + verificación manual de PHASE_STATUS_LABEL en 424-428 y línea 483
- NO se tocaron módulos protegidos ni tests; NO se ejecutó suite completa (cambio de solo texto UI, sin tests automatizados de frontend)

FASE 8G.3 / 2026-08-15
- acción: checkpoint de integración — ejecución de la suite completa tras acumular 5 fases (8F.1→8G.2) validadas solo de forma focalizada
- resultado: 577 passed, 0 failed, 0 errors (109.39s) — sin regresiones; sin cambios de código en este checkpoint
- validación: pytest tests/ modules/ -q (suite completa)

FASE 8G.2 / 2026-08-15
- problema: sin cobertura de orquestación autopilot real para las ramas editor/image_gen de _persist_chapter (deuda P3 de §19)
- solución: test nuevo tests/test_autopilot_persist_chapters.py que reutiliza el patrón del test writer: executor real (default_executor_factory + scheduler) con módulo editor STUB (sin tocar modules/editor/main.py) y módulo REAL image_generator (LocalImageProvider, sin LLM; rutas aisladas a tmp via IMAGE_STORAGE_ROOT/IMAGE_LOCAL_OUTPUT_DIR)
- validación: tests/test_autopilot_persist_chapters.py 2/2 PASS (test_editor_populates_edited_es_in_db, test_image_gen_populates_images_in_db) + sanity tests/test_autopilot_document_output.py 6/6 PASS (mismo patrón reutilizado) — suite completa no re-ejecutada

FASE 8F.4 / 2026-08-15
- problema: bug "or 3" ignoraba images_per_chapter=0 (0 se convertía en 3 en frontend_api.py:729, editorial.py:399/542/554, image_generator/main.py:172/265)
- solución: sustituido el patrón `X or 3` por comprobación explícita de None; image_planner no se tocó (ya manejaba 0); se incluyeron 2 líneas de image_generator (main.py:172 _build_simple_plan además de la 265) fuera de la lista original pero necesarias para el fix de extremo a extremo
- validación: test nuevo test_build_payload_preserves_num_images_zero (image_plan/image_gen 0→0, ausente→3) + comprobación manual generate_chapter_images (0→0, ausente→3) + regresión focalizada 60 PASS (test_image_generator.py, test_editorial_panel.py, test_frontend_api_autopilot.py 17/17, test_image_planner.py)

FASE 8F.3 / 2026-08-15
- problema: frontend mostraba solo 8/10 fases del pipeline real (image_plan/image_gen ausentes en AUTOPILOT_PHASES de app.js)
- solución: añadidas las 2 fases al array de app.js en la posición exacta del backend, más icono SVG nuevo; index.html sin cambios (renderizado dinámico)
- validación: comparación de arrays backend/frontend orden idéntico + test_frontend_api_autopilot.py 17/17 PASS + node --check OK
- hallazgo colateral: bug "or 3" que ignora images_per_chapter=0 (registrado como Problema Abierto #4, no corregido en este frente)

FASE 8F.2 / 2026-08-15
- problema: chapters.sources quedaba '[]' tras el writer (inconsistencia de modelo de datos); premisa original de §17 #1 sobreestimaba el riesgo para QC (que en realidad ya usaba SourceManager, no esta columna)
- solución: core/autopilot.py::_persist_chapter (rama writer/writer_en) persiste chapters.sources vía nuevo helper editorial.py::persist_chapter_sources
- validación: test nuevo PASS + regresión focalizada 29 tests PASS (test_autopilot_document_output.py, test_autopilot_editorial.py) — suite completa no re-ejecutada
- deuda descubierta: sin cobertura de orquestación autopilot para editor/image_gen vía executor real (ver §19)

FASE 8F.1 / 2026-08-15
- problema: metadata opcional (author/genre) bloqueaba Quality Gate; document_builder mostraba "Autor: Autor" en legal si author=None
- solución: QC baja author/genre/target_audience a WARNING (title+description siguen obligatorios); document_builder omite línea de autor ausente y usa book.title en copyright
- validación: 2 tests nuevos PASS + regresión focalizada 28 tests PASS (test_quality_gates.py, test_autopilot_quality_gate_payload.py, test_document_builder.py) — suite completa no re-ejecutada

FASE 7.9D.7 / 2026-08-11
- problema: chapter corto (<1500) / timeout / duplicados de continuación
- solución: control determinista del writer (backstop 100% Python, límites, guarda de duplicados)
- validación: E2E chapter deterministic ≥1500, sin placeholders

FASE 8E.x / 2026-08-12..14
- 8E.1: propagación de umbrales QC (min/target/max reales) -> test PASS
- 8E.2: metadata idea→description -> test PASS
- 8E.6: filename DOCX book_{book_id}_{lang} -> sin colisión
- 8E.7: integridad multi-libro DOCX 1:1 -> sin cross-book contamination
- 8E.8: E2E real refrescado, 8/8 PASS, QC PASS, DOCX PASS
- 7.9D.7 flaky resuelto: env de runner movido a main() -> 528 passed
```

**Nota:** no incluye cada commit, solo hitos. Los checkpoints intermedios de draft en
`data/checkpoints/1001/book/draft/v00xx.json` muestran la evolución del capítulo (de
~732 a 1668 palabras).

FASE checkpoint git-hygiene sesión 2026-08-27 (commits §17 #22/#23/#27/#28/#32/#33 + amend #35) / 2026-08-27
- qué se hizo: organización en 9 commits atómicos de código y tests ya validados en sesiones previas pero pendientes de commitear: §17 #22 (book_planner max_tokens), §17 #23+#28 (tests writer EN / topic_en fail-open), §17 #27+#33 (research: denylist redes sociales + filtro snippets SERP, código y tests), §17 #28 (capabilities imagen ES/EN + schemas + wiring), §17 #32 (providers seed opcional).
- hallazgo de auditoría: el commit original de §17 #35 F2 (gate diferenciado core/autopilot.py) había capturado sin querer código de §17 #28 (topic_en/resolución dinámica de capabilities de imagen) que estaba sin commitear en el mismo archivo. Corregido con `git commit --amend` del MENSAJE únicamente (sin tocar contenido) para que el registro de auditoría sea preciso: "fix(autopilot): §17 #35 F2 - gate diferenciado por error_type + persistencia quality_status (incluye §17 #28: resolución dinámica de capabilities de imagen por idioma / topic_en sin fallback ES, que estaba sin commitear en el mismo archivo)".
- validación: checkpoint de integración completo tras los 9 commits → 764 passed, 0 failed, 1 skipped (276.62s), sin regresión cruzada entre los fixes de la sesión.
FASE §17 #37 fix research query-frase larga (recuperación con query corto derivado) / 2026-08-28
- qué se hizo: en modules/research/main.py, si el query crudo (idea/título completo) devuelve 0 candidatos en Wikipedia/Wikidata, se deriva un query corto (corte en separador de cláusula + stopwords reutilizando _STOPWORDS_ES) y se reintenta UNA vez; topic, anclaje, denylists y filtro SERP sin cambios. commit 711874f + 2 tests nuevos (32 passed focalizados).
- validación: E2E real book_71 task_id=1883 → status=PASS, execution_mode=llm, fuentes reales vía query derivado; pipeline upstream completo PASS. quality_gate remanente falla por déficit de imágenes (§17 #30, evidencia anotada en esa fila).
- estado: FIXED + VALIDATED (E2E CONFIRMADO 2026-08-28).

FASE diseño retry-desde-origen (§17 #36, plan en 3 fases) / 2026-08-28
- decisión: retry tras FAIL de quality_gate debe poder volver a la fase real de origen, no solo re-ejecutar quality_gate. Plan en 3 fases independientes y commiteables por separado: (1) origin_phase estructurado en QualityControlItem [esta sesión], (2) resolver §17 #31 (persist_chapter_images merge) como precondición de seguridad antes de permitir reset de image_gen sin destruir imágenes válidas, (3) retry_job(from_phase=...) con cascade a fases dependientes + API/front.
- estado: Fase 1 en curso, ver commit siguiente.
FASE §17 #36 Fase 5 (UI reset_from_phase vía botón Reintentar existente) / 2026-08-28
- qué se hizo: retryAutopilot() en frontend/app.js ahora detecta origin_phase en checks FAIL de quality_gate.metrics (6 listas *_checks), elige el más upstream según AUTOPILOT_PHASES, y dispara POST /autopilot/reset (con confirm()) en vez de POST /autopilot/retry. Sin origin_phase disponible, comportamiento idéntico al anterior. Sin botón nuevo, sin cambios en index.html/backend.
- validación: node --check frontend/app.js OK; sin tests automatizados de JS en el proyecto.
- estado: CERRADO - §17 #36 completo (Fases 1-5)

- estado: CERRADO - VALIDADO. Historia de commits: c2c6f5e(F1)→600ffdb(F2,amend)→066011c(F3)→f04fc9d(#32)→24a280b(#27+#33 research)→f07ccb2(#22)→4aad04c(tests #23/#28)→df0d08d(#28 images)→02c711e(tests #27+#33)

# 25. AI HANDOFF

# HANDOFF FOR THE NEXT AI

```text
You are entering an existing production-like codebase (Space Lair).

Do NOT start by changing code.

First:
1. Read PROJECT_MASTER_STATUS.md.
2. Inspect the relevant files.
3. Verify that the requested functionality is actually missing/broken.
4. Identify the smallest safe change.
5. Do focused tests first.
6. Run E2E only when appropriate.
7. Do not run the full test suite unless this is an integration checkpoint.

Current known healthy pipeline:
  pipeline editorial (planner→research→outline→writer→fact_check→editor→image_plan→image_gen→quality_gate→docx)
  → DOCX en output/docx/book_{book_id}_{language}.docx. E2E real PASS (8/8, QC PASS).
  Chapter Writer con backstop determinista (≥1500 palabras, sin placeholders/duplicados).

Current known limitations:
  - Proveedor de imágenes activo = local placeholder (NO imagen IA real).
  - PDF = deuda OUT OF SCOPE.
  - Dependencia de Ollama (LLM local); fallbacks deterministas en writer/editor.
  - Process worker/server debe estar activo.

Current protected components:
  - modules/chapter_writer/main.py (PROTECTED)
  - tests/ (PROTECTED; no modificar para forzar PASS)
  - OUT_OF_SCOPE: research, fact_checker, editor, document_builder, quality_control,
    pdf_builder, image_generator, book_planner, translator, text_summarizer, word_counter
  - LIBRE: tools/

Current next priority:
  1. (P2) Alinear metadata author/genre/language en book_planner — mejora, no bug (ver Problema Abierto #2, §17).
  2. (P2) Traducir textos de interfaz aún en inglés. → RESUELTO (8H.1, ver §16/§23).
  3. (futuro) PDF sin colisión; proveedor de imágenes IA real.
```

