# SPACE LAIR — Manual de Instrucciones y estado del proyecto

> Sistema modular de generación editorial asistida por IA, con orquestación
> de tareas, workflows, proveedores de IA, SQLite, logging JSON y frontend web.

---

## 1. ¿Qué es Space Lair?

Space Lair es una **plataforma de orquestación editorial** que combina:

- **Módulos** especializados (`book_planner`, `text_summarizer`, `word_counter`, etc.).
- **Proveedores de IA** desacoplados (`ollama`, `openai_compatible`, `anthropic`).
- **Base de datos SQLite** con migraciones automáticas.
- **Cola de tareas** y scheduler para ejecución en segundo plano.
- **Workflows** para encadenar pasos.
- **Frontend web** + API.

El flujo general es:

1. El usuario o sistema **encola una tarea** con una *capability* y un *payload*.
2. El **scheduler** la asigna a un módulo capaz.
3. El **router/IA central** elige proveedor/módulo si hay varias opciones.
4. El **módulo** ejecuta y guarda **resultado, tokens y coste**.
5. Si aplica, queda **pendiente de aprobación humana** antes de seguir.

Todo se controla desde la **CLI** o el **frontend web**.

---

## 2. Estado del desarrollo

El proyecto avanzó por fases. Estado real actual:

| Fase | Descripción | Estado |
|------|-------------|--------|
| **Fase 0** | Núcleo y SQLite (tablas, migraciones, índices) | ✅ |
| **Fase 1** | Scheduler y módulos base (word_counter, text_summarizer) | ✅ |
| **Fase 2** | CLI (demo, serve, web, status, enqueue, approve, ...) | ✅ |
| **Fase 3** | Retries/backoff, health checks, Pydantic, timeouts | ✅ |
| **Fase 4** | Frontend 8-bit + API Flask + SSE | ✅ |
| **Fase 5** | Métricas de coste, logging JSON, eventos | ✅ |
| **Fase 6** | MCP bridge, workflows, JWT | ✅ |
| **Fase 7** | Proveedores Ollama / OpenAI-compatible / Anthropic | ✅ |
| **Fase 8** | Módulo `book_planner` + tests | ✅ |
| **Fase 9** | `SourceManager` + tests + documentación | ✅ |
| **Fase 10** | Módulo `chapter_writer` + tests | ✅ |
| **Fase 11** | Módulo `fact_checker` + tests | ✅ |
| **Fase 12** | Módulo `editor` + tests | ✅ |
| **Fase 13** | Módulo `translator` + tests | ✅ |
| **Fase 14** | Módulo `image_planner` + tests | ✅ |

---

## 3. Estructura del proyecto

```
SPACE LAIR/
├── core/
│   ├── __init__.py
│   ├── auth.py
│   ├── central_ai.py
│   ├── database.py
│   ├── events.py
│   ├── logger.py
│   ├── mcp_bridge.py
│   ├── metrics.py
│   ├── module_registry.py
│   ├── scheduler.py
│   ├── schemas.py
│   ├── task_queue.py
│   ├── workflow.py
│   ├── book/
│   │   ├── book_state.py
│   │   ├── book_schema.py
│   │   └── source_manager.py
│   └── providers/
│       ├── __init__.py
│       ├── base.py
│       ├── ollama.py
│       ├── openai_compatible.py
│       ├── anthropic.py
│       └── registry.py
├── modules/
│   ├── book_planner/
│   │   ├── main.py
│   │   └── module.json
│   ├── chapter_writer/
│   │   ├── main.py
│   │   └── module.json
│   ├── fact_checker/
│   │   ├── main.py
│   │   └── module.json
│   ├── editor/
│   │   ├── main.py
│   │   └── module.json
│   ├── translator/
│   │   ├── main.py
│   │   └── module.json
│   ├── image_planner/
│   │   ├── main.py
│   │   └── module.json
│   ├── mcp_demo/
│   ├── mcp_external/
│   ├── text_summarizer/
│   └── word_counter/
├── frontend/
│   ├── app.js
│   ├── frontend_api.py
│   ├── index.html
│   ├── style.css
│   └── static/
├── tests/
│   ├── __init__.py
│   ├── test_book_planner.py
│   ├── test_chapter_writer.py
│   ├── test_editor.py
│   ├── test_fact_checker.py
│   ├── test_source_manager.py
│   ├── test_translator.py
│   └── test_image_planner.py
├── data/
│   └── space_lair.db
├── examples/
│   └── workflow_demo.yaml
├── requirements.txt
├── run.py
├── .env.example
└── instrucciones.md
```

---

## 4. Configuración

### 4.1 Variables de entorno

Copiar `.env.example` a `.env` y completar:

```env
# Proveedor Ollama (modelo local)
OLLAMA_MODEL=llama3

# Proveedor OpenAI-compatible
OPENAI_COMPATIBLE_BASE_URL=http://localhost:11434/v1
OPENAI_COMPATIBLE_API_KEY=

# Proveedor Anthropic
ANTHROPIC_API_KEY=

# Base de datos (opcional)
SPACE_LAIR_DB_PATH=data/space_lair.db

# Logging
LOG_LEVEL=INFO
```

### 4.2 Dependencias

```bash
pip install -r requirements.txt
```

---

## 5. Ejecución

```bash
# Ejecutar tests
python -m pytest tests/ -v

# Ejecutar la aplicación (CLI + API)
python run.py
```

Comandos CLI útiles:

```bash
python run.py module list
python run.py module status
python run.py task enqueue --capability count_words --payload '{"text":"Hola"}'
python run.py task status
python run.py token
```

---

## 6. Proveedores implementados

### 6.1 Ollama (`core/providers/ollama.py`)

- Ejecuta `ollama run <modelo>` localmente.
- Configurable con `OLLAMA_MODEL`.

### 6.2 OpenAI-compatible (`core/providers/openai_compatible.py`)

- Usa `OPENAI_COMPATIBLE_BASE_URL` y `OPENAI_COMPATIBLE_API_KEY`.
- Sirve para endpoints compatibles con OpenAI (Ollama modo API, Together, etc.).

### 6.3 Anthropic (`core/providers/anthropic.py`)

- Usa `ANTHROPIC_API_KEY`.
- Compatible con Claude.

Todos heredan de `core/providers/base.py` y se registran en `registry.py`.

---

## 7. Módulo `book_planner`

- **Carpeta:** `modules/book_planner/`
- **Capability:** `create_book_plan`
- **Entrada:** idea, capítulos objetivo, idioma, audiencia, estilo, restricciones.
- **Salida:** título, subtítulo, descripción, capítulos planificados.
- **Persistencia:** usa `core/book/book_schema.py` y `core/database.py`.
- **Tests:** `tests/test_book_planner.py`

---

## 8. `SourceManager`

### 8.1 Propósito

Gestiona fuentes de investigación para capítulos/libros:

- Guardar fuentes con metadatos completos.
- Buscar por texto o tipo.
- Deduplicar por **hash SHA-256** de URL.
- Asociar fuentes a capítulos.
- Marcar fuentes verificadas o conflictivas.
- Calcular relevancia ajustada.
- Exportar a `sources.json`.

### 8.2 API principal

```python
from core.book.source_manager import SourceManager

# Guardar fuente
source = SourceManager.add_source(
    url="https://example.com/1",
    title="Ejemplo",
    source_type="official",
    relevance=7,
    chapter_ids=[1, 2],
)

# Buscar
results = SourceManager.search_sources(query="Python", source_type="academic")

# Asociar a capítulo
SourceManager.associate_chapter(source_id=1, chapter_id=3)

# Marcar estado
SourceManager.mark_verified(source_id=1)
SourceManager.mark_conflicting(source_id=1, "datos contradictorios")

# Exportar
SourceManager.export_sources_json("data/sources.json", chapter_id=1)
```

### 8.3 Reglas

- No se elimina una fuente si está asociada a capítulos.
- No se modifican `url` ni `url_hash` tras la creación.
- Deduplicación automática por hash.

---

## 9. Base de datos

### 9.1 Tablas principales

- `tasks`: tareas encoladas.
- `workflows`: definiciones de workflows.
- `workflow_steps`: pasos de workflows.
- `books`: proyectos editoriales.
- `chapters`: capítulos.
- `sources`: fuentes de investigación.

### 9.2 Migraciones

`core/database.py` incluye `_migrate(conn)` que aplica alters automáticamente
si faltan columnas. No requiere ejecutar scripts manuales.

### 9.3 Ruta

Por defecto `data/space_lair.db`. Se puede cambiar con `SPACE_LAIR_DB_PATH`.

---

## 10. Logging

- Configuración en `core/logger.py`.
- Formato JSON con campos: `timestamp`, `level`, `logger`, `message`, `task_id`, `module_id`, `capability`.
- Nivel configurable por `LOG_LEVEL`.

---

## 11. Tests

Ejecutar:

```bash
python -m pytest tests/ -v
```

Tests incluidos:

- `tests/test_book_planner.py`
- `tests/test_chapter_writer.py`
- `tests/test_editor.py`
- `tests/test_fact_checker.py`
- `tests/test_source_manager.py`
- `tests/test_translator.py`
- `tests/test_image_planner.py`

Cada test usa una BD temporal aislada (no toca `data/space_lair.db`).

---

## 12. Crear un módulo nuevo

### 12.1 Estructura

```
modules/mi_modulo/
├── module.json
└── main.py
```

### 12.2 `module.json`

```json
{
  "id": "mi_modulo",
  "name": "Mi Módulo",
  "description": "Hace mi módulo.",
  "type": "tool",
  "capabilities": ["mi_capability"],
  "requires_human_approval": false,
  "config": {
    "priority": 5,
    "timeout_seconds": 30,
    "provider": "anthropic",
    "model": "claude-sonnet-5"
  }
}
```

Campos obligatorios: `id`, `name`, `description`, `type` (`tool`|`agent`|`mcp`),
y `capabilities` (lista no vacía).

### 12.3 `main.py`

```python
def health_check() -> dict:
    return {"healthy": True}

def execute(payload: dict) -> dict:
    text = payload.get("text", "")
    return {"resultado": text.upper()}
```

`health_check()` es opcional pero recomendado.

### 12.4 Validación con Pydantic (opcional)

En `core/schemas.py`:

```python
class MiPayload(TaskPayload):
    text: str

PAYLOAD_SCHEMAS["mi_capability"] = MiPayload
```

Tras crear la carpeta, el módulo se carga automáticamente en el siguiente
arranque de `serve`/`web` (verifícalo con `python run.py module list`).

---

## 13. Troubleshooting

| Problema | Solución |
|----------|----------|
| No hay módulos cargados | Verifica `modules/` tiene subcarpetas con `module.json` y `main.py` válidos. |
| El resumidor falla | Necesita `ANTHROPIC_API_KEY` en `.env` **o** Ollama en `http://localhost:11434`. Revisa `module status`. |
| Módulo `unhealthy` | Usa `python run.py module status` para ver qué check falla. |
| Tareas bloqueadas en `running` | Se resetean automáticamente tras +300s. |
| Token inválido al aprobar | Genera uno nuevo con `python run.py token`. |
| Falta paquete `anthropic` | `pip install anthropic` (solo si usas Anthropic). |

---

## 14. Próximos pasos sugeridos

- Completar `web_researcher` con proveedor real.
- Añadir módulo `outline_generator`.
- Ampliar tests de integración.
- Conectar el frontend con la API central.
- Persistir resultados grandes en `data/results/`.

---

*Documento generado a partir del estado real del proyecto Space Lair.*

