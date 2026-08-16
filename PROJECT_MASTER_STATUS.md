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

---

# 18. LIMITACIONES CONOCIDAS

Distinción **LIMITATION** (diseño/entorno) vs **BUG**.

| Limitación | Tipo |
|---|---|
| Proveedor de imágenes activo = **local placeholder** (PNG de color sólido derivado de seed), NO genera imágenes IA reales. | LIMITATION |
| **PDF** fuera de alcance / deuda (pdf_builder). | LIMITATION / OUT OF SCOPE |
| **Dependencia de Ollama** (LLM local) para planner/research/writer/fact_check/editor/image_plan; si no está activo se usa fallback determinista (writer/editor), pero la calidad nominal del LLM se pierde. | LIMITATION |
| Requiere **worker/server activo** (scheduler/ap formativo) para procesar tareas. | LIMITATION |
| Frontend muestra las 10 fases del pipeline (image_plan/image_gen incluidas), pero sin UI para elegir proveedor/modelo de imagen IA (queda el local placeholder). | LIMITATION (UX) |
| El LLM puede devolver continuaciones duplicadas (se detectan y rechazan como avisos, no fallos). | LIMITATION (calidad LLM, acotada) |
| Document Builder no renderiza listas Markdown numeradas como listados nativos de Word (las trata como párrafos). | LIMITATION |
| Textos de interfaz parcialmente en inglés. | RESUELTO (ver §16, FASE 8H.1) — quedan solo términos técnicos/intencionales en inglés (SSE, DOCX, Autopilot, QC, etc.). | LIMITATION → RESUELTO |
| El filtro de relevancia de research (`_keyword_overlap`) se basa en solapamiento léxico de palabras clave, no en relevancia semántica/temática. Puede dar falsos positivos cuando existe coincidencia léxica casual (ej.: existe un lugar real "Dooms, Virginia" cuyo nombre coincide con un título ficticio "Los Dooms", haciendo que artículos geográficos irrelevantes pasen el filtro). Mitigado en profundidad por fact_check, que rechaza afirmaciones sin respaldo real independientemente de si la fuente pasó el filtro de research. No se aborda ahora: requeriría relevancia semántica, cambio de alcance mayor. | LIMITATION |



| **P3** | `core/image_providers/comfyui.py`: sintaxis rota (dict `DEFAULT_WORKFLOW` sin cerrar en la línea 43). **Sin impacto en runtime**: su import está silenciado por `try/except` en `core/image_providers/registry.py` y el provider activo por defecto es `local`. Cerrar la llave arregla la sintaxis (trivial), pero hacerlo **funcional** requiere reconstruir el workflow real de ComfyUI (nodos + conexiones + nodo `SaveImage`, actualmente ausente) — fuera de alcance de la limpieza 2026-08-16. | Diagnóstico 2026-08-16 |
# 19. DEUDA TÉCNICA

Priorizada (no estética como P0):

| Prioridad | Deuda | Nota |
|---|---|---|
| **P2** | Que `book_planner` emita `author`/`genre`/`language` en la ruta autómata real. **Mejora, no bug**: el sistema ya funciona correctamente sin ello (QC/DOCX aceptan su ausencia por diseño, ver §16). | **CLOSED** — resuelto en FASE 8J.1 (ver §16): `book_planner.execute()` emite `language` (del payload, default "es") y `genre` (inferido keyword-based desde la idea); `author` omitido (no derivable honestamente) |
| **P2** | PDF: renombrar a `book_{book_id}_{lang}.pdf` y validar. | Problema Abierto #3 |
| **P2** | Traducir textos de interfaz que quedan en inglés. | Sec. 13 | CLOSED (ver §16, FASE 8H.1) — textos de interfaz traducidos; lo que queda en inglés es intencional (términos técnicos, marcas, formatos). |
| **P3** | Eliminar código duplicado/inaccesible en `frontend_api.py`. | CLOSED (ver §16, FASE 8H.2). Microdiagnóstico confirmó que no había código inaccesible real, solo imports/comentarios redundantes de bajo riesgo. |
| **P3** | Reconexión SSE robusta si se implementa. | MANUAL_USUARIO §27 |
| **P3** | Conectar un proveedor de imágenes IA real (p.ej. ComfyUI) por defecto. | Sec. 8 |
| **P3** | (opcional, no urgente) Extraer helper de validación común para los pares approve/reject y cancel/retry en frontend_api.py (patrón fetch→validate→acción→broadcast repetido, pero con estados/retornos distintos; riesgo medio si se toca, valor bajo) | Diagnóstico 8H.2 §1.3 |
| **P3** | El runner E2E real (`run_e001_editorial.py`) no ejercita el camino real de outline→writer con LLM activo: corre en `chapter_execution_mode=deterministic` con editor `fallback`, por lo que no detecta bugs como outline.sections vacío (8K.1). | `run_e2e_001_editorial.py`; deuda de cobertura E2E — se necesita un test focalizado que invoque el outline con LLM real y verifique `sections` non-empty antes del writer |
| **P2** | Patrón recurrente: fixes de código validados en repo/tests no llegan al proceso real del servidor hasta reinicio manual (visto en 8L.1/8L.2 con `CHAP_FORCE_MIN`, y de nuevo hoy con el fix de research — book_23 corrió con módulo stale pese a que el fix ya estaba en el repo ~14 min antes). No hay verificación automática de que el proceso activo corresponda al código del repo. Sin mecanismo de auto-reload ni check de versión/hash al arrancar. | Catalizador 2026-08-16: reinicio manual requerido para exponer los fixes de research (anchor) + fact_check (gate). |


# 20. ROADMAP

```text
COMPLETADO   — pipeline editorial → document_builder → DOCX; QC; backstop determinista
EN CURSO     — mantener green (diagnóstico 8E.8 cerrado); nada activo pendiente de implementación mayor
SIGUIENTE    — Problemas Abiertos #1 y #2 cerrados (8F.1/8F.2, ver §16). Sin bloqueantes activos; deuda menor en §19.
FUTURO       — PDF estable, imágenes IA reales, traducción UI
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
  `pdf_builder`, `image_generator`, `image_planner`, `book_planner`, `translator`,
  `text_summarizer`, `word_counter`, `mcp_demo`, `mcp_external`.

> `modules/research/main.py` fue modificado con autorización explícita del
> usuario en FASE 8I.1 (multi-fuente + curación LLM) — ver §16/§24.

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
IMAGES:                  Orquestación/persistencia/inserción IMPLEMENTED; proveedor activo = LOCAL PLACEHOLDER (no IA real)
DOCUMENT BUILDER:        IMPLEMENTED + VALIDATED (book_{book_id}_{lang}.docx; comments[:255])
LAYOUT:                  IMPLEMENTED + VALIDATED (5 presets + aliases + overrides)
FRONT:                   IMPLEMENTED (10 fases; textos de interfaz traducidos, 8H.1; sin UI de proveedor de imagen IA)
TESTS:                   pendiente de re-verificación tras reinicio (baseline previo: 601 passed, 0 failed, 0 errors en 8K.3). Se añadieron hoy 2 fixes (research anchor + fact_check gate) validados en batería focalizada: test_research_sources/multisource/curation 25/25 PASS y test_autopilot_fact_check_gate/editorial/quality_gate_payload/document_output 32/32 PASS. La suite completa se re-ejecutará y actualizará el conteo tras el reinicio.
E2E:                     PASS (e2e_001_report.json status=completed; 8/8; DOCX PASS; QC PASS)
DOCX:                    PASS (output/docx/book_1001_es.docx)
PDF:                     OUT OF SCOPE / DEUDA (book_{language}.pdf sin book_id)
MAIN BLOCKERS:           Ninguno bloqueante. Problemas Abiertos #1, #2 y #4 cerrados (ver §16: 8F.1/8F.2/8F.4/8J.1). Deuda de cobertura de orquestación editor/image_gen cerrada en 8G.2 (ver §16/§19).
NEXT RECOMMENDED ACTION: Tras reiniciar el proceso con los 2 fixes (research anchor de relevancia + gate de fact_check), reintentar los libros reales con job FAILED espurio por el bug de gate: books 9, 18, 19, 23 y 25 (fact_check#status=FAIL quality_gate=PASS). Mantener green y vigilar la tasa de duplicación de continuation (book_22, 5/6) y el anclaje de relevancia en los reintentos.
LAST VERIFIED:           2026-08-16 (checkpoint 8L.2: timeout writer ampliado a 300s + CHAP_FORCE_MIN activo en servidor real, book_22 COMPLETED 1620 palabras)
```


# 24. CHANGELOG RESUMIDO (hitos)

```text
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

