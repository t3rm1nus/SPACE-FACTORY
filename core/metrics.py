"""Métricas de coste y consumo de tokens para Space Lair.

Proporciona:
- PRICE_TABLE: tabla de precios por proveedor/modelo (USD por 1M tokens).
- calculate_cost(provider, model, input_tokens, output_tokens) -> float.
- record_task_tokens(task_id, input_tokens, output_tokens, cost) -> None.
- extract_anthropic_usage(response) -> (input, output).
- summarize_costs() -> dict con coste total y por módulo.
"""

import logging
from typing import Any, Optional

from core import task_queue
from core.logger import get_logger

logger = get_logger(__name__)

# ============================================================
# Tabla de precios (USD por 1M tokens)
# Fuente: precios públicos de Anthropic (2025), Ollama es local (gratis)
# ============================================================
PRICE_TABLE = {
    "anthropic": {
        "claude-sonnet-5": {
            "input": 0.003,   # $3 por 1M tokens de input
            "output": 0.015,  # $15 por 1M tokens de output
        },
        "claude-opus-4": {
            "input": 0.015,
            "output": 0.075,
        },
        "claude-haiku-3": {
            "input": 0.00025,
            "output": 0.00125,
        },
    },
    "ollama": {
        # Los modelos de Ollama son locales; el coste es ~0
        # Se usa 0.0 para cálculos, pero se reportan tokens
        "llama3.1": {"input": 0.0, "output": 0.0},
        "llama2": {"input": 0.0, "output": 0.0},
        "mistral": {"input": 0.0, "output": 0.0},
        "qwen-agent:latest": {"input": 0.0, "output": 0.0},
        "default": {"input": 0.0, "output": 0.0},
    },
    "openai": {
        "gpt-4o": {"input": 0.005, "output": 0.015},
        "gpt-4-turbo": {"input": 0.010, "output": 0.030},
        "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    },
}


def get_model_pricing(provider: str, model: str) -> Optional[dict[str, float]]:
    """Obtiene los precios para un proveedor/modelo.

    Si el modelo no está en la tabla, intenta con 'default'.
    """
    provider_key = provider.lower()
    model_key = model.lower() if model else "default"

    if provider_key in PRICE_TABLE:
        models = PRICE_TABLE[provider_key]
        if model_key in models:
            return models[model_key]
        if "default" in models:
            return models["default"]
    # Fallback genérico
    return {"input": 0.0, "output": 0.0}


def calculate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """Calcula el coste de una llamada al LLM.

    Args:
        provider: 'anthropic', 'ollama', 'openai', etc.
        model: nombre del modelo (ej. 'claude-sonnet-5').
        input_tokens: número de tokens de entrada.
        output_tokens: número de tokens de salida.

    Returns:
        Coste en USD (float).
    """
    pricing = get_model_pricing(provider, model)
    if pricing is None:
        return 0.0

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)


def record_task_tokens(task_id: int, input_tokens: int = 0, output_tokens: int = 0, cost: float = 0.0) -> None:
    """Guarda los tokens y coste en la tarea correspondiente."""
    task_queue.update_task_metrics(task_id, input_tokens, output_tokens, cost)
    log(
        logger,
        logging.INFO,
        f"Tarea #{task_id}: {input_tokens}i/{output_tokens}o tokens, coste ${cost:.6f}",
        task_id=task_id,
    )


def extract_anthropic_usage(response: Any) -> tuple[int, int]:
    """Extrae input_tokens y output_tokens del response de Anthropic.

    El response puede ser:
    - Un objeto con .usage (SDK oficial)
    - Un dict con 'usage' (JSON)
    - Un dict plano con 'usage'
    """
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage", {})

    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
    elif hasattr(usage, "input_tokens"):
        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)
    else:
        input_tokens = 0
        output_tokens = 0

    return int(input_tokens), int(output_tokens)


def summarize_costs() -> dict[str, Any]:
    """Calcula el coste total y por módulo a partir de las tareas completadas."""
    conn_tasks = _get_completed_tasks_with_metrics()
    total_cost = 0.0
    total_in = 0
    total_out = 0
    by_module: dict[str, dict] = {}

    for task in conn_tasks:
        module_id = task.get("module_id") or "desconocido"
        cost = task.get("cost", 0.0) or 0.0
        t_in = task.get("tokens_input", 0) or 0
        t_out = task.get("tokens_output", 0) or 0

        total_cost += cost
        total_in += t_in
        total_out += t_out

        if module_id not in by_module:
            by_module[module_id] = {"cost": 0.0, "tokens_input": 0, "tokens_output": 0, "count": 0}
        by_module[module_id]["cost"] += cost
        by_module[module_id]["tokens_input"] += t_in
        by_module[module_id]["tokens_output"] += t_out
        by_module[module_id]["count"] += 1

    for module_id in by_module:
        by_module[module_id]["cost"] = round(by_module[module_id]["cost"], 6)

    return {
        "total_cost": round(total_cost, 6),
        "total_tokens_input": total_in,
        "total_tokens_output": total_out,
        "total_tasks_done": len(conn_tasks),
        "by_module": by_module,
    }


def _get_completed_tasks_with_metrics() -> list[dict]:
    """Obtiene las tareas done/error con sus métricas desde la BD."""
    from core.database import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, capability, module_id, status, cost, tokens_input, tokens_output
            FROM tasks
            WHERE status IN ('done', 'error')
            ORDER BY id DESC
            LIMIT 500
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def log(level: int, message: str, **kwargs: Any) -> None:
    """Compat wrapper for logger."""
    from core.logger import log as _log
    _log(logger, level, message, **kwargs)
