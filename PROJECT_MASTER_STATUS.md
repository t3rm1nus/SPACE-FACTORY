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
601 passed, 0 failed, 0 errors   (tests/ + modules/, incl. E2E runner) — 2026-08-16 (checkpoint 8K.3: fix tokenización/stopwords en _keyword_overlap, 142.61s)
```
Incrementos desde 8K.1: +1 test (`test_real_query_los_dooms_stopwords_filtro` en `tests/test_research_sources.py`, FASE 8K.3).
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
| quality_gate._check_images comparaba contra literal hardcodeado 3 en vez de books.image_count real — cualquier libro con image_count≠3 fallaba el gate de imágenes aunque el pipeline hubiera entregado exactamente las imágenes pedidas (evidencia real: libro 38, image_count=5, 5 imágenes/capítulo reales, QC FAIL "Imágenes por capítulo != 3") | 3 literales hardcodeados en modules/quality_control/main.py (_check_images, líneas ~419/425/432), nunca leía book.image_count | expected = clamp(book.image_count or 3, 0, 20); comparación y mensajes usan expected en vez de literal 3 | modules/quality_control/main.py, modules/quality_control/tests/test_quality_control.py | test_check_images_uses_book_image_count_not_literal (nuevo) + modules/quality_control/tests/ 8/8 PASS + tests/test_quality_gates.py 14/15 PASS (1 fallo preexistente y ajeno, ver deuda nueva §19); CONFIRMADO END-TO-END EN PRODUCCIÓN REAL 2026-08-22: retry de book_38 tras reinicio limpio → quality_gate check de imágenes "5 imágenes por capítulo" (PASS, antes '!=3'), job status=COMPLETED, output/docx/book_38_es.docx generado (7.505.053 bytes, 23:12:35). | **FIXED + VALIDATED** |

| Fuentes contaminadas temáticamente (ej. 'Latveria'/Marvel Comics en id 532) se reciclaban indefinidamente a libros futuros ajenos al tema vía add_source (dedupe por url_hash), sin volver a pasar por ningún filtro de anclaje — el filtro de research_web solo corre en la inserción original, nunca en la re-asociación. Evidencia real: book_39 'Historia del Mítico Juego Doom' heredó Latveria (insertada 2026-08-16 para book_23, antes de existir el fix de anclaje multi-palabra). | SourceManager.add_source reutiliza la fila existente y hace unión de chapter_ids sin re-validar contenido contra el topic del libro destino (core/book/source_manager.py, líneas 46-69). | 2 helpers nuevos en source_manager.py (get_source_by_url, book_ids_for_source); en core/autopilot.py, antes de add_source: si la fuente ya existe Y pertenece a otro book_id, se re-valida con _has_anchor_keyword (importado de modules/research/main.py, sin modificarlo) contra el topic/título del libro nuevo; si falla, no se asocia. Fuentes nuevas o ya del mismo libro: sin cambio de comportamiento. | core/book/source_manager.py, core/autopilot.py, tests/test_autopilot_editorial.py (test nuevo) | test_stale_source_not_reassociated_without_topic_anchor (nuevo, reproduce el caso real Latveria) + 46/46 PASS (test_autopilot_editorial.py, test_source_manager.py, test_autopilot_research_sources.py). Pendiente de confirmación en producción real tras reinicio del servidor. Confirmación E2E real con control positivo/negativo: book_42 (topic idéntico a book_40, colisión natural por url_hash) — 6 fuentes Doom legítimas SÍ reasociadas, 2 fuentes Marvel stale (Latveria id=640, Pantera Negra id=641) NO reasociadas. | Confirmación en producción 2026-08-23: ejercicio directo de la rama de rechazo (core/autopilot.py, cierre de fase research) contra datos reales — fuente id 640 (Latveria, url es.wikipedia.org/wiki/Latveria) propuesta deliberadamente para book_id=45 ('La Saga Doom: Historia del FPS...'); WARNING emitido: 'Fuente 640 (Latveria) NO asociada a book 45: sin anclaje temático (_has_anchor_keyword=False, libros previos [40])'; sources.id=640.chapter_ids sin cambios ([176,177,178]), book_45 solo asociado a sus 8 fuentes legítimas (663-670). Verificación hecha contra copia temporal de la BD (SPACE_LAIR_DB_PATH), producción real intacta. Script usado y archivado en tools/dev/archive/verify_anti_recycling_prod.py. | FIXED + VALIDATED + CONFIRMADO EN PRODUCCIÓN (2026-08-23) |
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
| 15 | fact_checker no detecta citas bibliográficas fabricadas: las fabricaciones del writer (#13) pasan sin ningún warning. | El gate de fact_check da falsa sensación de validación; las citas falsas llegan al DOCX sin señal alguna. | OPEN | Media (depende de #13 para ser relevante) | ch192 (con 3 citas APA falsas): fact_check devolvió claims_checked=1 con un único issue severidad INFO no relacionado; ch189 devolvió claims_checked=0 | Evaluar si fact_checker debería extraer y verificar referencias bibliográficas como tipo de claim aparte — sin diseño todavía; requiere autorización (OUT_OF_SCOPE) |
| 16 | ⚠️ CORRECCIÓN DE PREMISA (2026-08-23, diagnóstico solo-lectura): los DOCX book_46/book_47 SÍ existen hoy físicamente en output/docx/. Reportado originalmente como "DOCX fantasma" (PASS sin fichero); verificación posterior desmiente la ausencia. | Si la premisa se hubiera mantenido: docx_status=PASS dejaría de ser evidencia fiable. Al existir ambos ficheros, el riesgo actual se reduce a la falta de verificación post-save en document_builder (ver diagnóstico). | OPEN (premisa corregida; queda deuda menor de hardening) | Alta→Baja (tras corrección) | book_46_es.docx: CreationTime 23/08/2026 21:42:24 local, 759.619 bytes; book_47_es.docx: CreationTime 22:28:32-33 local, 12.866.341 bytes — coinciden exactamente con tasks BD 132 (created 2026-08-23 19:42:24 UTC) y 158 (20:28:32-33 UTC), offset +2h CEST. Último reinicio de servidor ~16:08 (server_err.log), anterior a ambas generaciones. Sin script de limpieza que toque output/docx/ encontrado en repo | Diagnóstico completo en sesión 2026-08-23: build_book_docx NO envuelve doc.save() en try/except ni verifica os.path.exists/tamaño tras guardar (main.py:699-707) — hardening recomendado, requiere autorización (OUT_OF_SCOPE) |
| 17 | Backstop determinista genera plantillas casi-verbatim repetidas DENTRO de un mismo capítulo (distinto de #7, que era duplicación exacta ENTRE capítulos, ya cerrado) — mismo patrón de frase aplicado a sujetos distintos en secciones consecutivas, con término musical incorrecto ("sonata" aplicado a bandas de rock) reutilizado sin variación semántica | Cosmético/calidad de redacción, no funcional | OPEN | Baja | book_48 cap2 (Queen/Led Zeppelin/Los Hermanos Rosales), mismo patrón de frase "creando una sonata única que cautivó a fans de todo el mundo" 3 veces | Sin acción por ahora — requeriría tocar chapter_writer/main.py (PROTECTED); no bloqueante |
| 18 | Imágenes en formato .webp no se insertan en el DOCX (warning "No se pudo insertar la imagen" durante build_book_docx, ~5 capítulos afectados en book_55) — python-docx no soporta webp nativamente | No bloqueante (la fase termina PASS, el capítulo queda sin esa imagen concreta) | CLOSED (ver §16, fix document_builder webp→PNG 2026-08-24) | Baja | book_55, warnings durante regeneración 2026-08-24 | Evaluar conversión webp→png/jpg antes de insertar en document_builder, o filtrar el formato en image_search aguas arriba — sin autorizar todavía |


---

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
| P3 | `image_planner/main.py:233` (`_build_prompt`) sigue usando el string antiguo `"Fotografía editorial, paleta coherente, detalle realista"` como guía de estilo textual para el LLM. No es un bug funcional (no se asigna a ningún campo de salida directamente; solo sobrevive al output si el LLM lo copia literalmente en su JSON), pero es inconsistente con el nuevo default `"realistic"`. | Trazado en sesión 2026-08-17 (fix FASE 8N.1); no corregido por estar fuera del alcance autorizado de esa tarea |
| **P2** | test_research_with_sources_passes (tests/test_quality_gates.py) roto por deriva: monkeypatch inyecta resmod.main.research_web que la refactor multi-fuente de modules/research/main.py ya no usa (cambios sin commit en el working tree de research, detectados 2026-08-22 durante validación del fix de quality_control). No es causado por ningún fix de esta sesión. | CLOSED (2026-08-22) — ver §16. Causa: deriva de contrato, los 3 fakes de research_web en tests/test_quality_gates.py usaban la firma vieja (query, max_sources, timeout) sin los kwargs language/topic añadidos por el refactor multi-fuente. Al invocarse con topic=..., el fake lanzaba TypeError capturado silenciosamente por execute(), y los 3 tests (incluyendo 2 que 'pasaban' aparentemente bien) verificaban en realidad la rama de excepción, no la lógica real del gate. |
| **P3** | generate_image (modules/image_generator/main.py) ignora el parámetro num_images y recorre TODO el image_plan en cada llamada, con skip_existing=True por defecto — esto fue la causa raíz de que la compensación de shortfall necesitara forzar skip_existing=False explícitamente. No es bloqueante hoy, pero es una superficie confusa: cualquier código que asuma 'pedí N, recibo N' está equivocado. | Detectado durante el diagnóstico del fix de dedup (2026-08-22, libro 36). Fuera de alcance de ese fix. Ver §16. |
| **P2** | Acabado DOCX no profesional: TOC sin números de página reales, enlaces markdown sin renderizar en 'Fuentes utilizadas' (aparecen como texto literal [texto](url)), fuentes duplicadas en el listado de un capítulo, numeración de Figura no secuencial entre capítulos, header/footer visible en portada mostrando el nombre de archivo interno, año de copyright hardcodeado. Evidencia: book_37.docx completo. Módulo: modules/document_builder/main.py (OUT_OF_SCOPE). | Detectado en diagnóstico book_37 (2026-08-22) | CLOSED (ver §16, ronda P2 document_builder: A1 año copyright + A2 portada sin header/footer + A3 footer sin filename + B1 hipervínculos reales + B3 numeración de figuras sin huecos, todos FIXED + VALIDATED con 24 passed). **B2 es una mitigación defensiva en el renderer (dedupe de líneas de fuente exactas), no corrige la causa raíz upstream en editor/chapter_writer** — causa raíz abierta como §17 #9. Queda pendiente menor dentro del acabado: TOC sin números de página reales (no abordado en esta ronda). |
| **P3** | docProps created/modified del DOCX quedan en el default de python-docx (2013-12-23T23:15:00Z) al no fijarse explícitamente en document_builder — cosmético, sin relación con el copyright (que sí es correcto). Evidencia: book_48. | Diagnóstico solo-lectura 2026-08-23: core_properties solo fija title/author/subject/comments/language (main.py:686-690); ni `core_properties.created` ni `.modified` aparecen en el archivo |
| **P3** | Títulos de capítulo generados por el fallback determinista de book_planner (`_short_idea_title` + "- Parte N") son legibles pero genéricos — los 24 capítulos de un mismo libro comparten la misma base de texto, diferenciados solo por el número | Mejora sobre el bug anterior (idea completa duplicada), no una solución de calidad editorial; solo el path LLM real del planner genera títulos temáticamente distintos por capítulo. No bloqueante. Evidencia: book_55 |




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
   - Estado: DIAGNOSED (2026-08-24) — CONFIRMADO: no existe wiring real.
   - `core/autopilot.py`: AUTOPILOT_PHASES fija la fase writer a capability="write_chapter_es" (hardcoded, línea ~58); "writer_en" existe como id reconocido en PER_CHAPTER_PHASES pero NUNCA se instancia como fase real; _run_single usa phase["capability"] directamente, sin ninguna rama condicional por books.languages.
   - `translator` (translate_es_en/translate_en_es): registrado en PAYLOAD_SCHEMAS/OUTPUT_SCHEMAS (core/schemas.py) pero SIN fase propia en AUTOPILOT_PHASES ni caso en build_phase_payload — módulo huérfano del pipeline.
   - Frontend: sin selector de idioma en index.html; app.js::createNewBook() nunca envía "language" en el POST; editorial.py::create_book() persiste siempre default "es" en books.languages.
   - Conclusión: para generar en inglés hace falta diseño nuevo — pendiente de decisión del arquitecto entre (a) traducir edited_es→en vía translator tras el pipeline es, o (b) escribir nativo con write_chapter_en seleccionado por books.languages. Ninguna se ha implementado.
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
WRITER:                  IMPLEMENTED + VALIDATED (backstop determinista; E2E 1668w ≥1500, sin placeholders)
RESEARCH:                IMPLEMENTED + VALIDATED (multi-fuente Wikipedia es/en + Wikidata + curación LLM opcional con fallback determinista, 8I.1); gate de orquestación corregido (8H.3)
IMAGES:                  Orquestación/persistencia/inserción IMPLEMENTED; proveedor activo = COMFYUI (SDXL Base+Refiner, IA REAL) default desde 2026-08-17; fallback a local si no responde; image_search_ratio: split IMPLEMENTED+VALIDATED en autopilot, BLOCKED para ratio>0 real por falta de schema en core/schemas.py; search_chapter_images YA registrado en PAYLOAD_SCHEMAS/OUTPUT_SCHEMAS (P1 cerrado 2026-08-18) — ratio>0 desbloqueado a nivel de validación de schema (aún sin exponer en el front).; image_search_ratio YA seleccionable desde el front (FASE 8N.3, 2026-08-18) — ciclo completo backend+front cerrado. Nota 2026-08-22: el split ahora deduplica por image_path (_dedupe_by_path) y fuerza generación nueva en la compensación (comp_payload['skip_existing']=False), corrigiendo la regresión del fix de shortfall que duplicaba la misma ruta en chapters.images (libro 36)., quality_gate ahora valida contra book.image_count real (fix confirmado en producción, book_38 COMPLETED con DOCX real)
BOOK PLANNER:               fallback determinista corregido 2026-08-24 (títulos de capítulo cortos, sin idea baked-in — ver §16)
DOCUMENT BUILDER:        IMPLEMENTED + VALIDATED (book_{book_id}_{lang}.docx; comments[:255]; sección de fuentes reconstruida determinísticamente desde chapters.sources desde 2026-08-23, ya no depende del texto del LLM — ver §16/§17 #9/#13/#14)
LAYOUT:                  IMPLEMENTED + VALIDATED (5 presets + aliases + overrides)
FRONT:                   IMPLEMENTED (10 fases; textos de interfaz traducidos, 8H.1; sin UI de proveedor de imagen IA)
TESTS:                   fix quality_control image_count: 8/8 PASS local + CONFIRMADO EN PRODUCCIÓN REAL (book_38 COMPLETED, DOCX generado). 1 fallo preexistente en test_quality_gates.py sigue abierto (ver §19 P2, no relacionado); fact_checker dedup fix confirmado (test_execute_dedupes_repeated_claims PASS, 14/14).
E2E:                     PASS (e2e_001_report.json status=completed; 8/8; DOCX PASS; QC PASS)
DOCX:                    PASS (output/docx/book_1001_es.docx)
PDF:                     OUT OF SCOPE / DEUDA (book_{language}.pdf sin book_id)
MAIN BLOCKERS:           Ninguno bloqueante. Problemas Abiertos #1, #2 y #4 cerrados (ver §16: 8F.1/8F.2/8F.4/8J.1). Deuda de cobertura de orquestación editor/image_gen cerrada en 8G.2 (ver §16/§19). Contaminación temática (§17 #6) CERRADA 2026-08-23. Fuentes duplicadas/fabricadas/omitidas en DOCX (§17 #9/#13/#14) CERRADAS a nivel de producto 2026-08-23; deuda residual de datos sucios en BD (writer) documentada, no bloqueante. #11/#12/#18 (relevancia temática de imágenes, falso warning de metadata, webp no insertable) CERRADOS 2026-08-24, sin bloqueantes activos.
NEXT RECOMMENDED ACTION: (pendiente de decisión — ver diagnóstico de barrido en §17/§19 de la sesión 2026-08-23). Nota de corrección: la entrada anterior de este campo afirmaba erróneamente que image_search carecía de denylist; se corrige aquí porque §17 #5 ya estaba FIXED + VALIDATED desde 2026-08-22 (_DOMAIN_DENYLIST en modules/image_search/main.py L.61-95/324-341, 11 dominios, aplicado antes de descargar tanto a img_src como a la página fuente). El error de redacción se debió a no recontrastar la ficha de §17 antes de escribir la acción; corregido tras diagnóstico de solo lectura de Cline el mismo día.
LAST VERIFIED (histórico): 2026-08-23 18:20 (sesión cerrada: regresión §17 #7 FIXED + VALIDATED con confirmación adicional en datos reales de producción — ejecución offline tasks 50/51 de book_43, de 4 párrafos verbatim duplicados a 0; limpieza de temporales realizada; servidor vivo fresco PID 8844).
LAST VERIFIED:           2026-08-23 (checkpoint documental: anti-reciclaje CONFIRMADO EN PRODUCCIÓN con ejercicio directo de la rama de rechazo — WARNING 'Fuente 640 NO asociada a book 45', BD producción intacta; fact_checker dedup registrado en §16/§24; servidor vivo PID 25380).
```


# 24. CHANGELOG RESUMIDO (hitos)

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

