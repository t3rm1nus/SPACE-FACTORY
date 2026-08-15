"""Módulo MCP de ejemplo: reverse_text.

Este módulo se expone como servidor MCP. La capability 'reverse_text'
se convierte en una tool MCP accesible vía POST /mcp/mcp_demo.
"""


def health_check() -> dict:
    """Verifica la salud del módulo mcp_demo."""
    return {
        "healthy": True,
        "status": "🟢 healthy",
    }


def execute(payload: dict) -> dict:
    """Invierte el texto recibido.

    Args:
        payload: dict con la clave 'text' (str).

    Returns:
        dict con el texto invertido.
    """
    text = payload.get("text", "")
    return {
        "reversed": text[::-1],
        "length": len(text),
    }