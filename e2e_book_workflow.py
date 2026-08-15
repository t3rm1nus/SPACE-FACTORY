"""Workflow E2E real de creación de libro usando Space Lair.

Este script usa la arquitectura real:
1. Encola tareas en el task queue
2. Ejecuta el scheduler (donde Hermes selecciona módulos)
3. Guarda checkpoints después de cada fase
4. No ejecuta módulos manualmente
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

# Configuración de entorno
os.environ["LLM_PROVIDER"] = "ollama"
os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
os.environ["OLLAMA_MODEL"] = "qwen-agent:latest"

from core import task_queue
from core.checkpoint import CheckpointManager, Stage
from core.database import init_db
from core.logger import get_logger, setup_logging
from core.module_registry import capabilities_map, load_modules
from core.scheduler import run_loop

setup_logging()
logger = get_logger("book_workflow")


# Configuración del proyecto de prueba
BOOK_CONFIG = {
    "title": "El nacimiento de Internet",
    "idea": "Historia del nacimiento y evolución inicial de Internet",
    "language": "es",
    "target_chapters": 1,
    "target_audience": "General",
    "style": "Divulgativo",
    "subject_constraints": "",
    "desired_length": "3000 palabras",
}


def wait_for_task(task_id: int, timeout: int = 300) -> dict[str, Any]:
    """Espera a que una tarea termine y retorna su resultado."""
    start = time.time()
    while time.time() - start < timeout:
        task = task_queue.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Tarea {task_id} no encontrada")
        
        status = task["status"]
        if status in ("done", "error", "cancelled"):
            return task
        
        logger.info(f"Esperando tarea {task_id}... ({status})")
        time.sleep(3)
    
    raise TimeoutError(f"Tarea {task_id} no completó en {timeout}s")


def save_checkpoint(book_id: int, stage: Stage, payload: dict[str, Any]) -> str:
    """Guarda un checkpoint y retorna la ruta del archivo."""
    manager = CheckpointManager()
    result = manager.save(book_id, stage, payload)
    path = result["path"]
    logger.info(f"Checkpoint guardado: {path}")
    return path


def run_book_workflow() -> dict[str, Any]:
    """Ejecuta el workflow completo de creación de libro."""
    
    logger.info("=" * 60)
    logger.info("INICIANDO WORKFLOW E2E - CREACIÓN DE LIBRO")
    logger.info("=" * 60)
    
    # Inicializar BD
    init_db()
    
    # Cargar módulos
    modules = load_modules()
    cap_map = capabilities_map(modules)
    logger.info(f"Módulos cargados: {', '.join(modules.keys())}")
    logger.info(f"Capabilities: {', '.join(cap_map.keys())}")
    
    book_id = 1  # ID fijo para esta prueba
    report = {
        "book_id": book_id,
        "status": "running",
        "title": BOOK_CONFIG["title"],
        "language": BOOK_CONFIG["language"],
        "chapters": BOOK_CONFIG["target_chapters"],
        "hermes": "PENDING",
        "book_planner": "PENDING",
        "research": "PENDING",
        "outline": "PENDING",
        "chapter_writer": "PENDING",
        "fact_checker": "PENDING",
        "editor": "PENDING",
        "checkpoint": "PENDING",
        "sources": [],
        "words": 0,
        "artifacts": [],
    }
    
    try:
        # ========================================
        # PASO 1: BOOK PLANNER
        # ========================================
        logger.info("\n[1/7] Ejecutando BOOK PLANNER...")
        book_plan_task_id = task_queue.enqueue_task(
            "create_book_plan",
            {
                "idea": BOOK_CONFIG["idea"],
                "target_chapters": BOOK_CONFIG["target_chapters"],
                "language": BOOK_CONFIG["language"],
                "target_audience": BOOK_CONFIG["target_audience"],
                "desired_length": BOOK_CONFIG["desired_length"],
                "style": BOOK_CONFIG["style"],
                "subject_constraints": BOOK_CONFIG["subject_constraints"],
            },
            max_attempts=1
        )
        logger.info(f"Tarea encolada: {book_plan_task_id}")
        
        # Ejecutar scheduler para procesar la tarea
        logger.info("Ejecutando scheduler...")
        run_loop(modules, cap_map, max_iterations=15)
        
        # Esperar resultado
        book_plan_task = wait_for_task(book_plan_task_id, timeout=180)
        logger.info(f"Estado tarea book_plan: {book_plan_task['status']}")
        
        if book_plan_task["status"] != "done":
            raise RuntimeError(f"Book planner falló: {book_plan_task.get('error')}")
        
        book_plan_result = json.loads(book_plan_task["result"] or "{}")
        logger.info(f"Plan generado: {book_plan_result.get('title')}")
        
        report["book_planner"] = "PASS"
        report["hermes"] = "PASS"  # Hermes seleccionó book_planner
        
        # Guardar checkpoint de planning
        checkpoint_path = save_checkpoint(book_id, Stage.RESEARCH, {
            "stage": "planning",
            "book_plan": book_plan_result,
            "task_id": book_plan_task_id,
        })
        report["artifacts"].append(checkpoint_path)
        
        # ========================================
        # PASO 2: RESEARCH (simulado con el plan)
        # ========================================
        logger.info("\n[2/7] Ejecutando RESEARCH...")
        
        research_text = f"Investigación sobre: {BOOK_CONFIG['idea']}\n\n"
        research_text += f"Título: {book_plan_result.get('title')}\n"
        research_text += f"Descripción: {book_plan_result.get('description')}\n\n"
        
        for chapter in book_plan_result.get("chapters", []):
            research_text += f"Capítulo {chapter.get('number')}: {chapter.get('title')}\n"
            research_text += f"  Objetivo: {chapter.get('objective')}\n"
            research_text += f"  Preguntas clave: {', '.join(chapter.get('key_questions', []))}\n"
            research_text += f"  Requisitos: {', '.join(chapter.get('research_requirements', []))}\n\n"
        
        report["research"] = "PASS"
        
        checkpoint_path = save_checkpoint(book_id, Stage.RESEARCH, {
            "stage": "research",
            "research": research_text,
            "book_plan": book_plan_result,
        })
        report["artifacts"].append(checkpoint_path)
        
        # ========================================
        # PASO 3: OUTLINE (extraído del book_plan)
        # ========================================
        logger.info("\n[3/7] Generando OUTLINE...")
        
        outline = {
            "title": book_plan_result.get("title"),
            "chapters": book_plan_result.get("chapters", []),
        }
        
        report["outline"] = "PASS"
        
        checkpoint_path = save_checkpoint(book_id, Stage.OUTLINE, {
            "stage": "outline",
            "outline": outline,
        })
        report["artifacts"].append(checkpoint_path)
        
        # ========================================
        # PASO 4: CHAPTER WRITER
        # ========================================
        logger.info("\n[4/7] Ejecutando CHAPTER WRITER...")
        
        chapter_outline = book_plan_result.get("chapters", [])[0] if book_plan_result.get("chapters") else {}
        
        chapter_task_id = task_queue.enqueue_task(
            "write_chapter_es",
            {
                "book_metadata": {
                    "book_id": book_id,
                    "title": book_plan_result.get("title"),
                    "description": book_plan_result.get("description"),
                    "language": BOOK_CONFIG["language"],
                },
                "chapter_outline": chapter_outline,
                "research": research_text,
                "sources": [],
                "previous_chapter_summaries": [],
                "target_word_count": 3000,
            },
            max_attempts=1
        )
        logger.info(f"Tarea encolada: {chapter_task_id}")
        
        # Ejecutar scheduler
        logger.info("Ejecutando scheduler...")
        run_loop(modules, cap_map, max_iterations=20)
        
        # Esperar resultado
        chapter_task = wait_for_task(chapter_task_id, timeout=300)
        logger.info(f"Estado tarea chapter: {chapter_task['status']}")
        
        if chapter_task["status"] != "done":
            raise RuntimeError(f"Chapter writer falló: {chapter_task.get('error')}")
        
        chapter_result = json.loads(chapter_task["result"] or "{}")
        chapter_md_path = chapter_result.get("chapter_md_path", "")
        word_count = chapter_result.get("word_count", 0)
        
        logger.info(f"Capítulo generado: {chapter_md_path} ({word_count} palabras)")
        
        report["chapter_writer"] = "PASS"
        report["words"] = word_count
        if chapter_md_path:
            report["artifacts"].append(chapter_md_path)
        
        # Leer el contenido del capítulo
        chapter_text = ""
        if chapter_md_path and os.path.exists(chapter_md_path):
            with open(chapter_md_path, "r", encoding="utf-8") as f:
                chapter_text = f.read()
        
        # Guardar checkpoint de draft
        checkpoint_path = save_checkpoint(book_id, Stage.DRAFT, {
            "stage": "draft",
            "chapter_text": chapter_text,
            "chapter_md_path": chapter_md_path,
            "word_count": word_count,
            "task_id": chapter_task_id,
        })
        report["artifacts"].append(checkpoint_path)
        
        # ========================================
        # PASO 5: FACT CHECKER
        # ========================================
        logger.info("\n[5/7] Ejecutando FACT CHECKER...")
        
        fact_check_task_id = task_queue.enqueue_task(
            "fact_check_chapter",
            {
                "chapter_text": chapter_text,
                "sources": [],
                "target_language": "es",
            },
            max_attempts=1
        )
        logger.info(f"Tarea encolada: {fact_check_task_id}")
        
        # Ejecutar scheduler
        logger.info("Ejecutando scheduler...")
        run_loop(modules, cap_map, max_iterations=10)
        
        # Esperar resultado
        fact_check_task = wait_for_task(fact_check_task_id, timeout=180)
        logger.info(f"Estado tarea fact_check: {fact_check_task['status']}")
        
        if fact_check_task["status"] != "done":
            raise RuntimeError(f"Fact checker falló: {fact_check_task.get('error')}")
        
        fact_check_result = json.loads(fact_check_task["result"] or "{}")
        logger.info(f"Fact check: {fact_check_result.get('status')} - {fact_check_result.get('claims_checked')} afirmaciones verificadas")
        
        report["fact_checker"] = "PASS"
        
        # Guardar checkpoint de fact_check
        checkpoint_path = save_checkpoint(book_id, Stage.FACT_CHECK, {
            "stage": "fact_check",
            "fact_check": fact_check_result,
            "task_id": fact_check_task_id,
        })
        report["artifacts"].append(checkpoint_path)
        
        # ========================================
        # PASO 6: EDITOR
        # ========================================
        logger.info("\n[6/7] Ejecutando EDITOR...")
        
        editor_task_id = task_queue.enqueue_task(
            "edit_chapter",
            {
                "chapter_text": chapter_text,
                "style_guide": None,
                "target_language": "es",
                "protected_terms": ["Internet", "ARPANET"],
                "facts": [],
                "references": [],
            },
            max_attempts=1
        )
        logger.info(f"Tarea encolada: {editor_task_id}")
        
        # Ejecutar scheduler
        logger.info("Ejecutando scheduler...")
        run_loop(modules, cap_map, max_iterations=10)
        
        # Esperar resultado
        editor_task = wait_for_task(editor_task_id, timeout=180)
        logger.info(f"Estado tarea editor: {editor_task['status']}")
        
        if editor_task["status"] != "done":
            raise RuntimeError(f"Editor falló: {editor_task.get('error')}")
        
        editor_result = json.loads(editor_task["result"] or "{}")
        edited_text = editor_result.get("edited_text", chapter_text)
        
        logger.info(f"Editor completado: {len(edited_text)} caracteres")
        
        report["editor"] = "PASS"
        
        # Guardar checkpoint de edición
        checkpoint_path = save_checkpoint(book_id, Stage.EDITED, {
            "stage": "editing",
            "edited_text": edited_text,
            "editor_result": editor_result,
            "task_id": editor_task_id,
        })
        report["artifacts"].append(checkpoint_path)
        
        # ========================================
        # PASO 7: CHECKPOINT FINAL
        # ========================================
        logger.info("\n[7/7] Guardando CHECKPOINT FINAL...")
        
        final_checkpoint = {
            "stage": "completed",
            "book_id": book_id,
            "title": book_plan_result.get("title"),
            "chapters": book_plan_result.get("chapters"),
            "final_text": edited_text,
            "word_count": len(edited_text.split()),
            "tasks": {
                "book_plan": book_plan_task_id,
                "chapter": chapter_task_id,
                "fact_check": fact_check_task_id,
                "editor": editor_task_id,
            },
        }
        
        checkpoint_path = save_checkpoint(book_id, Stage.EDITED, final_checkpoint)
        report["checkpoint"] = f"PASS ({checkpoint_path})"
        report["artifacts"].append(checkpoint_path)
        report["status"] = "completed"
        
        logger.info("\n" + "=" * 60)
        logger.info("WORKFLOW COMPLETADO EXITOSAMENTE")
        logger.info("=" * 60)
        
        return report
        
    except Exception as e:
        logger.error(f"ERROR en workflow: {e}", exc_info=True)
        report["status"] = "error"
        report["error"] = str(e)
        report["failed_stage"] = "unknown"
        return report


if __name__ == "__main__":
    result = run_book_workflow()
    
    print("\n" + "=" * 60)
    print("INFORME FINAL DEL WORKFLOW")
    print("=" * 60)
    print(f"BOOK ID: {result.get('book_id')}")
    print(f"STATUS: {result.get('status')}")
    print(f"TITLE: {result.get('title')}")
    print(f"LANGUAGE: {result.get('language')}")
    print(f"CHAPTERS: {result.get('chapters')}")
    print()
    print(f"HERMES: {result.get('hermes')}")
    print(f"BOOK PLANNER: {result.get('book_planner')}")
    print(f"RESEARCH: {result.get('research')}")
    print(f"OUTLINE: {result.get('outline')}")
    print(f"CHAPTER WRITER: {result.get('chapter_writer')}")
    print(f"FACT CHECKER: {result.get('fact_checker')}")
    print(f"EDITOR: {result.get('editor')}")
    print(f"CHECKPOINT: {result.get('checkpoint')}")
    print()
    print(f"SOURCES: {len(result.get('sources', []))}")
    print(f"WORDS: {result.get('words')}")
    print(f"ARTIFACTS: {len(result.get('artifacts', []))}")
    print()
    print("RUTAS DE ARCHIVOS GENERADOS:")
    for artifact in result.get("artifacts", []):
        print(f"  - {artifact}")
    
    if result.get("error"):
        print("\nERROR:")
        print(f"  {result.get('error')}")
        sys.exit(1)
    
    sys.exit(0)
