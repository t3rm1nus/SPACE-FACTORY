# PROJECT MASTER STATUS — SPACE LAIR / LIVING AI FACTORY

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
`AUTOPILOT_PHASES`) incluye las 10 fases de arriba. El Frontend y el runner E2E
(`run_e2e_001_editorial.py`) muestran/ejecutan 8 fases (sin `image_plan`/`image_gen`,
porque el E2E usa `image_count=0`).

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
REAL AI IMAGE PROVIDER: NO — el proveedor activo por defecto es LOCAL PLACEHOLDER
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
  `DEFAULT_PROVIDER = "local"` → `LocalImageProvider`
  (`core/image_providers/local.py`). **Este proveedor genera un PNG placeholder de
  color sólido (derivado de semilla/hash del prompt) usando SOLO stdlib — NO es una
  imagen IA real.** Opcional: `ComfyUiProvider` (requiere servidor ComfyUI; se registra
  bajo demanda).
- **Persistencia en capítulo:** `frontend/editorial.py::persist_chapter_images` escribe
  las rutas en `chapters.images`.
- **Consumo en DOCX:** `document_builder` inserta cada ruta de imagen en su capítulo con
  caption.

> **MUY IMPORTANTE:** No "imágenes implementadas" a secas. La **orquestación**, la
> **persistencia** y la **inserción DOCX** están implementadas, pero el
> **proveedor de imágenes real activo genera placeholders** (no IA real). No hay un
> proveedor de imágenes IA real conectado por defecto.

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
`planner, research, outline, writer, fact_check, editor, quality_gate, docx` →
**8 fases** (espejo del backend **pero sin** `image_plan`/`image_gen`).

> ⚠️ **Discrepancia de IDs:** el frontend representa 8 fases y el Autopilot tiene 10
> (`image_plan`, `image_gen` faltan en el front). No es un error sino una omisión
> consciente; documentar si se amplía el front para imágenes.

### Estados que muestra
- Job: `PENDING/RUNNING/FAILED/COMPLETED/CANCELLED` (mostrados como ESPERANDO/EN
  EJECUCIÓN/CANCELADO/COMPLETADO/FALLIDO).
- Fase: `PENDING/RUNNING/RETRY/PASS/FAIL` (+ reintento).
- Feed SSE: `phase_started/completed/failed`, `job_completed/failed`, `task_*`,
  `central_ai_decision`.

### Limitaciones conocidas del front
- Solo en español/parcialmente; algunos textos en inglés ("NEW BOOK", etc.).
- No expone la configuración de imágenes IA (proveedor) en la UI.
- Pipe visual no incluye las 2 fases de imagen.


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

### LAST KNOWN FULL SUITE (registrado, NO re-ejecutado aquí)
```text
571 passed, 0 failed, 0 errors   (tests/ + modules/, incl. E2E runner)
```
También referenciado: "503 tests verdes" excluyendo el E2E pesado
(`test_runner_e2e_001.py`).

---

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

> **Distinción clave:** `e2e_output.txt` muestra una **ejecución ANTERIOR/stale** donde el
> **document_builder FALLÓ** (`ValueError: exceeded 255 char limit for property` en
> `core_properties.comments`). Ese error **ya está corregido** (`[:255]`), y el reporte
> canónico actual es PASS. **No mezclar** como "E2E fallido".

### E2E Front
Validado por tests de integración (`tests/test_frontend_api_autopilot.py`,
`test_frontend_api_docx.py`) que fijan el comportamiento del endpoint DOCX (200/404/409/400).

---

# 16. PROBLEMAS RESUELTOS

| Problema | Causa | Fix | Archivos | Validación | Estado |
|---|---|---|---|---|---|
| Editor timeout (tareas muertas) | LLM lento agotaba timeout scheduler 180s sin fallback | `EDITOR_PROVIDER_TIMEOUT=60`, `EDITOR_MAX_RETRIES=1`, `_fallback_edit` (devuelve original) | modules/editor/main.py | tests/test_editor.py + E2E (fallback) | **FIXED + VALIDATED** |
| Writer timeout + duplicados de continuación | LLM lento + continuaciones repetidas sin control | `WRITER_PROVIDER_TIMEOUT=60`, `WRITER_TOTAL_TIME_BUDGET=150`, `ABSOLUTE_HARD_LIMIT=8`, duplicate guard + backstop determinista | modules/chapter_writer/main.py | test_chapter_writer + E2E (1668w, det.) | **FIXED + VALIDATED** |
| Flaky tests 7.9D.7 | `os.environ['CHAP_FORCE_MIN']` en import-time de runner contaminaba pytest | Env movido a `_configure_environment()` en `main()` | run_e2e_001_editorial.py | 528 passed en tests/ | **FIXED + VALIDATED** |
| DOCX `comments` >255 chars → ValueError | Librería python-docx limita a 255 | `comments=(description or "")[:255]` | modules/document_builder/main.py | test + E2E PASS | **FIXED + VALIDATED** |
| DOCX filename colisión cross-book | Nombre `book_es.docx` sin book_id | `book_{book_id}_{language}.docx` | modules/document_builder/main.py | test_e2e_docx_integration (8E.6/8E.7) | **FIXED + VALIDATED** |
| Metadata no llegaba a QC (FAIL) | UI enviaba solo title+idea; idea no → description | `create_book` mapea `idea`→`description`; preserva metadata explícita | frontend/editorial.py | test_editorial_metadata (8E.2) | **FIXED + VALIDATED** |
| Fuentes globales no se propagaban (Q gate #3) | QC usaba fuentes de job en vez de asociaciones reales | `_chapter_source_urls()` desde SourceManager; `_build_book_dict` incluye `sources` por capítulo | frontend/editorial.py | test_editorial_sources (8D.2) | **FIXED + VALIDATED** |
| Umbrales QC usaban defaults 20/30/40 | build_phase_payload no pasaba min/target/max del libro | `build_phase_payload` propaga min/target/max reales | core/autopilot.py | test_autopilot_quality_gate_payload (8E.1) | **FIXED + VALIDATED** |
| Título de capítulo duplicado en DOCX | Speaker/LLM duplicaba heading | parseo controlado de markdown + headings canónicos | modules/document_builder/main.py | tests | **FIXED + VALIDATED** |


---

# 17. PROBLEMAS ABIERTOS

> Solo problemas **realmente detectados** en código/repositorio, no supuestos.

| # | Problema | Impacto | Estado | Prioridad | Evidencia | Próximo paso |
|---|---|---|---|---|---|---|
| 1 | **`chapters.sources` no se puebla** por el chapter_writer (la columna existe, `database.py`, pero queda `'[]'`). `_build_book_dict` obtiene fuentes reales desde `SourceManager`, no de esta columna. | El QC `_check_sources` depende de `chapters[].sources`; en el autómata real (no runner) podría dar FAIL si `_chapter_source_urls` no recuperara las asociaciones. En el E2E pasa porque el runner construye `book_dict` manual con fuentes. | OPEN (propuesta PROB 3 de `tools/dev/propuesta_reconciliacion.md`) | Alta | `propuesta_reconciliacion.md` §Cambio #1; `frontend/editorial.py` | Persistir `json.dumps(sources)` en `chapters.sources` tras writer; requiere aprobación (PROTECTED) |
| 2 | **`book_planner.execute` no devuelve `author`/`genre`/`language`** → en el autómata real, `create_book` INSERTa NULL y el QC "Metadatos completos" podría FAIL. El E2E pasa porque inyecta metadata explícita. | QC metadata FAIL en pipeline autómata real (no runner). | OPEN (propuesta PROB 1) | Alta | `propuesta_reconciliacion.md`; `modules/book_planner/main.py` (salida); `modules/quality_control/main.py` `_check_book` | Alinear planner o la ruta de creación de book_dict |
| 3 | **PDF builder usa `book_{language}.pdf`** (colisión análoga a la del DOCX pre-8E.6). | Colisión de entregables si se generan varios libros PDF con el mismo idioma. | DEBT / OUT OF SCOPE | Baja (no bloqueante) | `state.json`/`PROJECT_STATUS.md` KNOWN_BAD | Corregir a `book_{book_id}_{lang}.pdf` cuando se retome PDF |

---

# 18. LIMITACIONES CONOCIDAS

Distinción **LIMITATION** (diseño/entorno) vs **BUG**.

| Limitación | Tipo |
|---|---|
| Proveedor de imágenes activo = **local placeholder** (PNG de color sólido derivado de seed), NO genera imágenes IA reales. | LIMITATION |
| **PDF** fuera de alcance / deuda (pdf_builder). | LIMITATION / OUT OF SCOPE |
| **Dependencia de Ollama** (LLM local) para planner/research/writer/fact_check/editor/image_plan; si no está activo se usa fallback determinista (writer/editor), pero la calidad nominal del LLM se pierde. | LIMITATION |
| Requiere **worker/server activo** (scheduler/ap formativo) para procesar tareas. | LIMITATION |
| Frontend expone solo 8 fases (no image_plan/image_gen). | LIMITATION (UX) |
| El LLM puede devolver continuaciones duplicadas (se detectan y rechazan como avisos, no fallos). | LIMITATION (calidad LLM, acotada) |
| Document Builder no renderiza listas Markdown numeradas como listados nativos de Word (las trata como párrafos). | LIMITATION |
| Textos de interfaz parcialmente en inglés. | LIMITATION |



# 19. DEUDA TÉCNICA

Priorizada (no estética como P0):

| Prioridad | Deuda | Nota |
|---|---|---|
| **P1** | Persistir `chapters.sources` y completar metadatos (author/genre) en la ruta autómata real, para que el Quality Gate no dependa de la ruta manual del runner E2E. | Problemas Abiertos #1 y #2 |
| **P2** | PDF: renombrar a `book_{book_id}_{lang}.pdf` y validar. | Problema Abierto #3 |
| **P2** | Frontend: exponer las fases `image_plan`/`image_gen` para reflejar el pipeline real de 10 fases. | Sec. 13 |
| **P2** | Traducir textos de interfaz que quedan en inglés. | Sec. 13 |
| **P3** | Eliminar código duplicado/inaccesible en `frontend_api.py`. | MANUAL_USUARIO §27 |
| **P3** | Reconexión SSE robusta si se implementa. | MANUAL_USUARIO §27 |
| **P3** | Conectar un proveedor de imágenes IA real (p.ej. ComfyUI) por defecto. | Sec. 8 |


# 20. ROADMAP

```text
COMPLETADO   — pipeline editorial → document_builder → DOCX; QC; backstop determinista
EN CURSO     — mantener green (diagnóstico 8E.8 cerrado); nada activo pendiente de implementación mayor
SIGUIENTE    — resolver Problemas Abiertos #1/#2 (persistencia sources + metadata en autómata real)
FUTURO       — PDF estable, imágenes IA reales, frontend 10 fases, traducción UI
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


# 21. ARCHIVOS PROTEGIDOS

Fuente: `tools/dev/config.py` + `tools/dev/security.py` (real, vigente).

### NO TOCAR (PROTECTED_FILES)
- `modules/chapter_writer/main.py` — protegido; solo cambios aprobados (fase 7.9D.7).
- `tests/` — NO modificar tests para forzar PASS (regla de validación #1).

### TOCAR SOLO CON AUTORIZACIÓN (OUT_OF_SCOPE_MODULES)
- `research`, `fact_checker`, `editor`, `document_builder`, `quality_control`,
  `pdf_builder`, `image_generator`, `book_planner`, `translator`, `text_summarizer`,
  `word_counter`.

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
IMAGES:                  Orquestación/persistencia/inserción IMPLEMENTED; proveedor activo = LOCAL PLACEHOLDER (no IA real)
DOCUMENT BUILDER:        IMPLEMENTED + VALIDATED (book_{book_id}_{lang}.docx; comments[:255])
LAYOUT:                  IMPLEMENTED + VALIDATED (5 presets + aliases + overrides)
FRONT:                   IMPLEMENTED (8 fases; sin image_plan/gen; sin UI de proveedor de imagen)
TESTS:                   Última suite registrada: 571 passed, 0 failed, 0 errors
E2E:                     PASS (e2e_001_report.json status=completed; 8/8; DOCX PASS; QC PASS)
DOCX:                    PASS (output/docx/book_1001_es.docx)
PDF:                     OUT OF SCOPE / DEUDA (book_{language}.pdf sin book_id)
MAIN BLOCKERS:           Ninguno bloqueante; pendientes: persistir chapters.sources (#1) y metadata en autómata real (#2)
NEXT RECOMMENDED ACTION: Mantener green; alinear autómata real a los overrides del runner (persistencia sources + metadata)
LAST VERIFIED:           2026-08-14 23:23:41 (E2E) / 2026-08-15 (este documento)
```


# 24. CHANGELOG RESUMIDO (hitos)

```text
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
  1. Persistir chapters.sources (cierra QC sources en autómata real) — requiere aprobación.
  2. Alinear metadata (author/genre) en la ruta autómata real.
  3. (futuro) PDF sin colisión; imágenes IA reales; frontend 10 fases.
```

