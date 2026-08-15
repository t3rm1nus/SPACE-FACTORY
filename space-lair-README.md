🚀 Space Lair - Checklist de Desarrollo
📋 ÍNDICE
Fase 0: Base de Datos y Core

Fase 1: Scheduler y Módulos

Fase 2: CLI y Comandos

Fase 3: Mejoras Críticas

Fase 4: Frontend 8-Bit

Fase 5: Mejoras Importantes

Fase 6: Ampliaciones v1

🗄️ FASE 0: BASE DE DATOS Y CORE
0.1 Database (core/database.py)
□ Crear archivo core/database.py
□ Definir función get_db() que retorna conexión SQLite
□ Crear tabla tasks con columnas:
id INTEGER PRIMARY KEY AUTOINCREMENT

capability TEXT NOT NULL

payload TEXT NOT NULL (JSON)

status TEXT NOT NULL (pending/running/done/error/pending_approval)

module_id TEXT

result TEXT (JSON)

error TEXT

attempts INTEGER DEFAULT 0

max_attempts INTEGER DEFAULT 1

created_at DATETIME DEFAULT CURRENT_TIMESTAMP

started_at DATETIME

finished_at DATETIME

cost REAL DEFAULT 0.0

tokens_input INTEGER DEFAULT 0

tokens_output INTEGER DEFAULT 0

□ Crear función init_db() que crea la tabla si no existe
□ Añadir índices: idx_status, idx_capability, idx_module_id
□ Función reset_stale_running_tasks() para resetear tareas 'running' al arrancar
0.2 Task Queue (core/task_queue.py)
□ Crear archivo core/task_queue.py
□ enqueue_task(capability, payload, max_attempts=1) → INSERT en tasks
□ get_next_pending(capability=None) → SELECT pending ORDER BY created_at LIMIT 1
□ start_task(task_id, module_id) → UPDATE status='running', started_at=NOW
□ complete_task(task_id, result) → UPDATE status='done', result, finished_at
□ fail_task(task_id, error) → UPDATE status='error', error, finished_at
□ approve_task(task_id) → UPDATE status='pending' (desde pending_approval)
□ reject_task(task_id) → UPDATE status='error', error='Rechazada por humano'
□ mark_for_approval(task_id) → UPDATE status='pending_approval'
□ get_task(task_id) → SELECT * FROM tasks WHERE id=?
□ all_tasks() → SELECT * FROM tasks ORDER BY id DESC
□ increment_attempts(task_id) → UPDATE attempts=attempts+1
□ reset_stale_running() → UPDATE status='pending' WHERE status='running' AND started_at < NOW()-300
0.3 Module Registry (core/module_registry.py)
□ Crear archivo core/module_registry.py
□ Función load_modules() → escanea modules/ y carga cada carpeta con module.json
□ Validar module.json tiene: id, name, description, type, capabilities
□ Cargar main.py de cada módulo con importlib
□ Retornar dict: {module_id: {"manifest": {...}, "execute": function, "path": "..."}}
□ Función capabilities_map(modules) → {capability: [module_id, module_id, ...]}
□ Función get_module(module_id) → retorna módulo por ID
□ Función health_check(module_id) → verifica dependencias (API keys, etc.)
⚙️ FASE 1: SCHEDULER Y MÓDULOS EJEMPLO
1.1 Scheduler (core/scheduler.py)
□ Crear archivo core/scheduler.py
□ Importar logging y configurar logger
□ Función run_loop(modules, cap_map, max_iterations=None) → bucle principal
□ En cada iteración:
□ Para cada capability en cap_map:
□ Obtener tarea pending con get_next_pending(capability)
□ Si hay tarea:
□ Obtener módulos que pueden ejecutar esa capability
□ Si 1 módulo → seleccionar directamente
□ Si >1 módulo → llamar a central_ai.choose_module()
□ Si módulo requiere aprobación → marcar pending_approval
□ Sino → ejecutar módulo con timeout
□ Manejar éxito/error/retry
□ Añadir sleep entre iteraciones (1 segundo)
□ Loggear cada acción con nivel INFO/ERROR
□ Manejar KeyboardInterrupt para salida limpia
1.2 Central AI (core/central_ai.py)
□ Crear archivo core/central_ai.py
□ Función choose_module(capability, modules, payload) → retorna module_id
□ Si 1 módulo → retornar ese
□ Si hay ANTHROPIC_API_KEY → llamar a Anthropic con ROUTER_MODEL
□ Prompt: "Dados estos módulos: {descriptions}, ¿cuál es mejor para {payload}?"
□ Retornar module_id elegido
□ Si no hay API key → usar priority de module.json (fallback)
□ Loggear decisión tomada
1.3 Módulos Ejemplo
word_counter (modules/word_counter/)
□ Crear carpeta modules/word_counter/
□ Crear modules/word_counter/module.json:
json
{
  "id": "word_counter",
  "name": "Contador de palabras",
  "description": "Cuenta palabras y caracteres de un texto. No usa IA.",
  "type": "tool",
  "capabilities": ["count_words"],
  "requires_human_approval": false,
  "config": {"priority": 5, "timeout_seconds": 10}
}
□ Crear modules/word_counter/main.py con execute(payload):
□ Extraer 'text' del payload
□ Contar palabras y caracteres
□ Retornar dict con resultados
text_summarizer (modules/text_summarizer/)
□ Crear carpeta modules/text_summarizer/
□ Crear modules/text_summarizer/module.json:
json
{
  "id": "text_summarizer",
  "name": "Resumidor de texto",
  "description": "Usa LLM para resumir texto en 2-3 frases.",
  "type": "agent",
  "capabilities": ["summarize_text"],
  "requires_human_approval": false,
  "config": {
    "priority": 5,
    "timeout_seconds": 60,
    "provider": "anthropic",
    "model": "claude-sonnet-5",
    "ollama_model": "llama3.1"
  }
}
□ Crear modules/text_summarizer/main.py:
□ execute(payload) → extraer 'text'
□ _call_anthropic(prompt) → usar Anthropic API
□ _call_ollama(prompt) → usar Ollama local
□ Manejar error si no hay API key (RuntimeError claro)
🖥️ FASE 2: CLI Y COMANDOS
2.1 Run.py (run.py)
□ Crear archivo run.py
□ Importar dotenv y cargar .env
□ Comando demo:
□ Inicializar DB
□ Cargar módulos
□ Encolar tareas de ejemplo
□ Ejecutar scheduler con max_iterations=10
□ Mostrar status final
□ Comando serve:
□ Inicializar DB
□ Cargar módulos
□ Ejecutar scheduler en bucle infinito
□ Comando approve <id>:
□ Llamar a task_queue.approve_task()
□ Mostrar mensaje de confirmación
□ Comando reject <id>:
□ Llamar a task_queue.reject_task()
□ Mostrar mensaje de confirmación
□ Comando status:
□ Mostrar todas las tareas en tabla formateada
□ Incluir colores (opcional)
□ Comando enqueue <capability> <payload_json> (NUEVO):
□ Parsear JSON del payload
□ Llamar a task_queue.enqueue_task()
□ Mostrar ID de tarea creada
2.2 Configuración (.env)
□ Crear .env.example:
text
ANTHROPIC_API_KEY=
ROUTER_MODEL=claude-haiku-4-5-20251001
OLLAMA_URL=http://localhost:11434/api/generate
LOG_LEVEL=INFO
□ Copiar a .env en setup
🔥 FASE 3: MEJORAS CRÍTICAS
3.1 Sistema de Retries
□ Modificar task_queue.enqueue_task() para aceptar max_attempts parameter
□ Modificar scheduler.run_loop():
□ Capturar excepción del módulo
□ Llamar a task_queue.increment_attempts(task_id)
□ Si attempts < max_attempts → volver a pending
□ Si attempts >= max_attempts → marcar error definitivo
□ Añadir backoff exponencial: pending con next_retry_at = NOW() + (2 ** attempts) seconds
□ Modificar get_next_pending() para filtrar por next_retry_at <= NOW()
3.2 Timeout por Módulo
□ Añadir timeout_seconds al module.json (por defecto 30)
□ Modificar scheduler.run_loop():
□ Usar concurrent.futures.ThreadPoolExecutor
□ future.result(timeout=module.timeout_seconds)
□ Capturar TimeoutError y manejarlo como error
□ Loggear timeout con nivel WARNING
3.3 Estado "Running"
□ Modificar scheduler.run_loop():
□ Antes de ejecutar: task_queue.start_task(task_id, module_id)
□ Después de ejecutar: task_queue.complete_task() o fail_task()
□ Al arrancar serve: task_queue.reset_stale_running_tasks()
□ Añadir columna started_at a la BD (ya incluida en Fase 0)
3.4 Logging Estructurado
□ Crear core/logger.py:
□ Configurar logging con formato JSON o estructurado
□ Niveles: DEBUG, INFO, WARNING, ERROR
□ Añadir task_id, module_id, capability a cada log
□ Reemplazar todos los print() por logger.info(), logger.error(), etc.
□ Añadir variable LOG_LEVEL en .env
🎮 FASE 4: FRONTEND 8-BIT
4.1 Estructura del Frontend
□ Crear carpeta frontend/
□ Crear frontend/index.html - página principal
□ Crear frontend/style.css - estilo retro 8-bit
□ Crear frontend/app.js - lógica del frontend
□ Crear frontend/static/ para assets (sprites, sonidos)
4.2 Diseño Visual Retro
□ Fuente: "Press Start 2P" de Google Fonts
□ Paleta de colores:
Fondo: #1a1a2e (azul oscuro)

Texto: #00ff41 (verde matrix)

Bordes: #ff6b35 (naranja retro)

Alertas: #ff004d (rojo neón)

Éxito: #00ff41 (verde)

□ Efectos:
□ Pixel border con image-rendering: pixelated
□ Scanline overlay con CSS
□ Parpadeo de texto tipo CRT
□ Sonidos 8-bit (opcional)
4.3 Visualización de Módulos
□ Grid de módulos con sprites:
□ word_counter → sprite de 📝 (cuaderno pixelado)
□ text_summarizer → sprite de 🤖 (robot)
□ Módulos type "tool" → 🛠️ (herramienta)
□ Módulos type "agent" → 🧠 (cerebro)
□ Cada módulo muestra:
□ Nombre
□ Estado: 🟢 Activo / 🔴 Inactivo / ⚡ Procesando
□ Capacidades que ofrece
□ Última tarea procesada
4.4 El Megarobot Central
□ Sprite del "Megarobot" en el centro de la pantalla
□ Animación:
□ Ojos parpadean al procesar tareas
□ Antenas se mueven cuando la IA central decide
□ Luces RGB según estado
□ Display LED mostrando:
□ Tareas totales procesadas
□ Módulos activos
□ Última decisión de la IA central
4.5 Workers Animados
□ Cada módulo tiene un "worker" (personaje pixelado)
□ Workers se mueven del módulo al megarobot:
□ Cuando una tarea está en pending → worker recoge paquete 📦
□ Cuando tarea en running → worker corre con el paquete
□ Cuando tarea en done → worker entrega ✅
□ Cuando tarea en error → worker tropieza 💥
□ Animaciones en canvas con requestAnimationFrame
4.6 Panel de Control
□ Sidebar izquierdo: Lista de tareas en tiempo real
□ Sidebar derecho: Logs del sistema (scrollable)
□ Bottom bar: Comandos rápidos
□ Botón "Encolar tarea de prueba"
□ Botón "Aprobar tarea" (con selector)
□ Botón "Rechazar tarea"
□ Top bar: Métricas
□ Tareas/minuto
□ Módulos activos
□ Coste estimado (si hay API key)
4.7 API para el Frontend
□ Crear frontend_api.py (nuevo módulo core)
□ Endpoints:
□ /api/tasks → GET lista de tareas
□ /api/modules → GET lista de módulos
□ /api/stats → GET estadísticas
□ /api/enqueue → POST encolar tarea
□ /api/approve/<id> → POST aprobar
□ /api/reject/<id> → POST rechazar
□ /api/stream → WebSocket o SSE para updates en tiempo real
□ Servir frontend desde un puerto (ej: 8080)
4.8 Actualización en Tiempo Real
□ Usar Server-Sent Events (SSE) o WebSocket
□ Eventos a emitir:
□ task_created → nuevo módulo worker animado
□ task_started → worker corre
□ task_completed → worker entrega
□ task_failed → worker tropieza
□ central_ai_decision → megarobot parpadea
□ Frontend escucha eventos y actualiza UI
4.9 Sprites Pixel Art (generados con CSS/Canvas)
□ Crear sprite para "worker" (persona pixelada)
□ Crear sprite para "megarobot"
□ Crear sprite para "paquete" 📦
□ Crear sprite para "módulo" (edificio/estación)
□ Animaciones:
□ Idle (quieto)
□ Walking (caminando)
□ Running (corriendo)
□ Working (trabajando)
□ Celebration (celebrando)
□ Fail (tropeciendo)
4.10 Sonidos 8-bit (opcional)
□ Crear archivos de audio:
□ coin.wav - tarea completada ✅
□ error.wav - tarea falló ❌
□ approve.wav - aprobación humana 👍
□ robot.wav - IA central decide 🤖
□ walk.wav - worker camina 🚶
□ Reproducir con Web Audio API
🚀 FASE 5: MEJORAS IMPORTANTES
5.1 Validación de Payload
□ Instalar Pydantic: pip install pydantic
□ Crear core/schemas.py con:
□ TaskPayload base
□ CountWordsPayload con text: str
□ SummarizePayload con text: str, max_words: Optional[int]
□ Modificar módulos para usar schemas
□ Validar payload antes de pasar al módulo
5.2 Health Checks
□ Añadir health_check() opcional en módulos
□ En module_registry.py:
□ check_all_health() → recorre módulos y verifica
□ Marcar módulos como "unhealthy" si fallan
□ No enviar tareas a módulos unhealthy
□ En frontend: mostrar estado de salud 🟢/🟡/🔴
5.3 Métricas de Coste
□ Añadir columnas a DB: cost, tokens_input, tokens_output
□ En central_ai.py: calcular coste de llamada a Anthropic
□ En módulos agent: reportar tokens usados
□ En frontend: mostrar coste total y por módulo
□ Crear core/metrics.py:
□ calculate_cost(provider, model, input_tokens, output_tokens)
□ Tabla de precios por modelo
5.4 CLI Mejorada
□ Instalar click: pip install click
□ Refactorizar run.py con decoradores de click:
python
@click.group()
def cli():
    pass

@cli.command()
def demo():
    ...

@cli.command()
@click.option('--port', default=8080)
def web(port):
    ...
□ Comandos anidados: space-lair task list, space-lair module status
🔮 FASE 6: AMPLIACIONES V1
6.1 Dependencias por Módulo
□ Crear modules/mi_modulo/requirements.txt
□ En module_registry.py:
□ install_module_dependencies(module_id) → pip install -r
□ ensure_module_env(module_id) → crear venv propio
□ Cargar módulo desde su propio venv
6.2 Resultados Grandes (Archivos)
□ Modificar task_queue.complete_task():
□ Si result > 1MB → guardar en data/results/{task_id}.json
□ Guardar solo la ruta en la BD
□ Crear core/storage.py:
□ save_result(task_id, data) → guarda en archivo
□ load_result(task_id) → carga desde archivo
6.3 Autenticación para approve/reject
□ Crear core/auth.py:
□ Generar tokens JWT
□ verify_token(token) → valida
□ require_auth() decorator
□ Modificar CLI: approve <id> --token <jwt>
□ Modificar API: requerir token en headers
6.4 MCP (Model Context Protocol) Support
□ Crear core/mcp_bridge.py
□ Convertir módulo a servidor MCP:
□ Leer manifest MCP
□ Exponer herramientas vía MCP
□ Llamar a servidores MCP externos
□ En module_registry.py: detectar si módulo es MCP
6.5 Workflows (DSL básico)
□ Crear core/workflow.py:
□ Parsear YAML con steps
□ Ejecutar secuencia, paralelo, condiciones
□ execute_workflow(workflow_def, initial_payload)
□ Ejemplo de workflow:
yaml
steps:
  - id: step1
    capability: summarize_text
  - id: step2
    capability: translate_text
    depends_on: step1
  - id: step3
    capability: generate_image
    parallel: [step1, step2]
📦 ARCHIVOS FINALES DEL PROYECTO
text
space-lair/
├── .env
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
├── run.py
│
├── core/
│   ├── __init__.py
│   ├── database.py
│   ├── task_queue.py
│   ├── module_registry.py
│   ├── scheduler.py
│   ├── central_ai.py
│   ├── logger.py
│   ├── schemas.py
│   ├── metrics.py
│   ├── storage.py
│   └── auth.py
│
├── modules/
│   ├── word_counter/
│   │   ├── module.json
│   │   └── main.py
│   └── text_summarizer/
│       ├── module.json
│       └── main.py
│
├── frontend/
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   ├── frontend_api.py
│   └── static/
│       ├── sprites/
│       │   ├── worker.png
│       │   ├── megarobot.png
│       │   ├── module.png
│       │   └── package.png
│       └── sounds/
│           ├── coin.wav
│           ├── error.wav
│           ├── approve.wav
│           └── robot.wav
│
├── data/
│   ├── space_lair.db
│   └── results/
│
└── tests/
    ├── test_database.py
    ├── test_task_queue.py
    ├── test_scheduler.py
    └── test_modules.py
🎯 ORDEN DE IMPLEMENTACIÓN RECOMENDADO
Fase 0 → Base de datos y core (fundación)

Fase 1 → Scheduler y módulos ejemplo (mínimo viable)

Fase 2 → CLI y comandos (usabilidad)

Fase 3 → Mejoras críticas (robustez)

Fase 4 → Frontend 8-bit (visibilidad y diversión) 🎮

Fase 5 → Mejoras importantes (calidad)

Fase 6 → Ampliaciones v1 (escalabilidad)

💡 TIPS PARA CURSOR AI
Archivo por archivo: Dale a Cursor un archivo a la vez con instrucciones específicas

Pruebas unitarias: Pide a Cursor que escriba tests para cada función

Documentación inline: Pide docstrings y comentarios en cada función

Refactorización: Después de cada fase, pide a Cursor revisar código duplicado

Frontend iterativo: Primero el HTML estático, luego CSS, luego JavaScript, luego animaciones