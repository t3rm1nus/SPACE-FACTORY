"""MCP (Model Context Protocol) Bridge para Space Lair.

Proporciona soporte para:
1. Detectar módulos MCP (type="mcp" o campo "mcp" en module.json).
2. Convertir un módulo de Space Lair en servidor MCP — expone sus
   capabilities como herramientas (tools) vía JSON-RPC 2.0 sobre HTTP.
3. Llamar a servidores MCP externos mediante tools/call remoto.

El protocolo MCP usa JSON-RPC 2.0 sobre HTTP/SSE. Este bridge implementa
los métodos esenciales del protocolo:
  - initialize
  - tools/list
  - tools/call
"""

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Optional

from core.logger import get_logger

logger = get_logger(__name__)

# Métodos MCP soportados
MCP_INITIALIZE = "initialize"
MCP_TOOLS_LIST = "tools/list"
MCP_TOOLS_CALL = "tools/call"

# Claves del manifest MCP
MCP_TRANSPORT = "transport"
MCP_SERVER_URL = "server_url"
MCP_ENABLE_SERVER = "serve"
MCP_HEADERS = "headers"

# Versión del protocolo MCP
PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    """Error del protocolo MCP."""


def is_mcp_module(module: dict[str, Any]) -> bool:
    """Detecta si un módulo es MCP.

    Un módulo se considera MCP si:
      - manifest.type == "mcp", o
      - manifest tiene la clave "mcp" (config MCP).
    """
    manifest = module.get("manifest", {})
    return manifest.get("type") == "mcp" or "mcp" in manifest


def get_mcp_config(module: dict[str, Any]) -> dict:
    """Extrae y valida la config MCP de un módulo.

    El manifest puede declarar MCP de dos formas:
      - type: "mcp" + campos top-level (transport, server_url, serve, headers)
      - type: "mcp" + mcp: { transport, server_url, serve, headers }

    Returns:
        dict con la configuración MCP normalizada.
    """
    manifest = module.get("manifest", {})
    mcp_block = manifest.get("mcp", {})
    config = dict(mcp_block) if isinstance(mcp_block, dict) else {}

    # Si type es mcp y no hay bloque anidado, usar campos top-level
    if manifest.get("type") == "mcp" and not config:
        for key in (MCP_TRANSPORT, MCP_SERVER_URL, MCP_ENABLE_SERVER, MCP_HEADERS):
            if key in manifest:
                config[key] = manifest[key]

    config.setdefault(MCP_TRANSPORT, "http")
    config.setdefault(MCP_ENABLE_SERVER, False)
    config.setdefault(MCP_HEADERS, {})

    # Validar transporte
    transport = config.get(MCP_TRANSPORT)
    if transport not in ("http", "sse"):
        raise ValueError(f"Transporte MCP no soportado '{transport}' (válidos: http, sse)")

    return config


def server_info(module: dict[str, Any]) -> dict:
    """Devuelve la información del servidor MCP (respuesta a initialize)."""
    manifest = module["manifest"]
    caps = manifest.get("capabilities", [])
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {
                "listChanged": False,
            }
        },
        "serverInfo": {
            "name": manifest.get("id", "space-lair-module"),
            "version": manifest.get("version", "0.1.0"),
        },
        "tools": [
            {
                "name": cap,
                "description": manifest.get("description", ""),
                "inputSchema": {"type": "object", "properties": {}},
            }
            for cap in caps
        ],
    }


def _local_tool_call(module: dict[str, Any], name: str, arguments: dict) -> Any:
    """Ejecuta una tool local del módulo (llama a execute() si es capability)."""
    manifest = module["manifest"]
    caps = manifest.get("capabilities", [])
    if name not in caps:
        raise MCPError(f"Tool '{name}' no disponible en módulo '{manifest.get('id', '?')}'")

    execute = module.get("execute")
    if not callable(execute):
        raise MCPError(f"Módulo '{manifest.get('id', '?')}' no tiene execute()")

    logger.debug(
        "MCP tool local: %s.%s args=%r",
        manifest.get("id"),
        name,
        arguments,
    )
    return execute(arguments or {})


def _remote_tool_call(config: dict, name: str, arguments: dict) -> Any:
    """Llama a una tool en un servidor MCP externo (JSON-RPC sobre HTTP)."""
    server_url = config.get(MCP_SERVER_URL)
    transport = config.get(MCP_TRANSPORT, "http")

    if not server_url:
        raise MCPError("Módulo MCP externo requiere 'server_url' en manifest")

    # En transporte SSE, el endpoint de mensajes suele ser /message
    if transport == "sse" and server_url.endswith("/sse"):
        server_url = server_url.replace("/sse", "/message")

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": MCP_TOOLS_CALL,
        "params": {
            "name": name,
            "arguments": arguments or {},
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    headers.update(config.get(MCP_HEADERS, {}) or {})

    data = json.dumps(request).encode("utf-8")
    http_request = urllib.request.Request(
        server_url, data=data, headers=headers, method="POST"
    )

    try:
        with urllib.request.urlopen(http_request, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            response = _parse_mcp_response(body, server_url)
    except urllib.error.HTTPError as e:
        error_body = b""
        try:
            error_body = e.read()
        except Exception:
            pass
        raise MCPError(f"MCP externo HTTP {e.code}: {error_body.decode('utf-8', 'replace')}") from e
    except urllib.error.URLError as e:
        raise MCPError(f"No se pudo conectar a MCP externo {server_url}: {e.reason}") from e
    except TimeoutError as e:
        raise MCPError(f"Timeout llamando a MCP externo {server_url}") from e

    if not isinstance(response, dict):
        raise MCPError(f"Respuesta MCP externa no válida: {response!r}")

    if "error" in response:
        err = response["error"]
        raise MCPError(f"Error MCP externo: {err}")

    result = response.get("result", {})
    # MCP: result puede contener content=[{type:'text', text:'...'}] y/o structuredContent
    if isinstance(result, dict):
        if "structuredContent" in result:
            return result["structuredContent"]
        if "content" in result:
            return _extract_text_content(result["content"])
    return result


def _parse_mcp_response(body: str, server_url: str) -> Any:
    """Parsea la respuesta MCP (JSON directo o stream SSE)."""
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass

    # SSE: extraer el primer 'data: {...}'
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    continue

    raise MCPError(f"Respuesta MCP no válida de {server_url}: {body[:200]}")


def _extract_text_content(content: list) -> str:
    """Extrae texto de MCP content: [{type: 'text', text: '...'}, ...]."""
    texts = []
    for item in content or []:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "\n".join(texts) if texts else content


def call_tool(module: dict[str, Any], name: str, arguments: Optional[dict] = None) -> Any:
    """Llama a una tool del módulo (local o externa según config MCP).

    Args:
        module: Módulo cargado {manifest, execute, path}.
        name: Nombre de la tool (capability).
        arguments: Argumentos de la tool.

    Returns:
        Resultado de la tool MCP.
    """
    config = get_mcp_config(module)

    # Si hay server_url, llamar al servidor MCP externo
    if config.get(MCP_SERVER_URL):
        return _remote_tool_call(config, name, arguments or {})

    # Servidor local: ejecutar execute() del módulo
    return _local_tool_call(module, name, arguments or {})


def handle_jsonrpc_request(module: dict[str, Any], request_body: Any) -> dict:
    """Procesa un request JSON-RPC MCP y devuelve la respuesta.

    Soporta los métodos: initialize, tools/list, tools/call.
    """
    if not isinstance(request_body, dict):
        return _jsonrpc_error(None, -32700, "Parse error: se esperaba un objeto JSON")

    method = request_body.get("method")
    request_id = request_body.get("id")

    if method == MCP_INITIALIZE:
        return _jsonrpc_result(request_id, server_info(module))

    if method == MCP_TOOLS_LIST:
        return _jsonrpc_result(request_id, {
            "tools": server_info(module)["tools"],
        })

    if method == MCP_TOOLS_CALL:
        params = request_body.get("params", {}) or {}
        name = params.get("name")
        arguments = params.get("arguments", {}) or {}

        if not name:
            return _jsonrpc_error(request_id, -32602, "Falta 'name' en params")

        try:
            result = call_tool(module, name, arguments)
            return _jsonrpc_result(request_id, {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, default=str),
                    }
                ],
                # structuredContent para consumo programático
                "structuredContent": result,
            })
        except MCPError as e:
            return _jsonrpc_error(request_id, -32000, str(e))

    return _jsonrpc_error(request_id, -32601, f"Método no soportado: {method}")


def _jsonrpc_result(request_id: Any, result: Any) -> dict:
    """Construye una respuesta JSON-RPC exitosa."""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict:
    """Construye una respuesta JSON-RPC de error."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def create_mcp_http_handler(modules: dict[str, dict[str, Any]]):
    """Crea un manejador WSGI que sirve los módulos MCP locales.

    Expone cada módulo MCP con 'serve: true' en:
      GET  /mcp                -> lista de servidores MCP disponibles
      POST /mcp/<module_id>    -> endpoint JSON-RPC MCP

    Returns:
        Función WSGI: (environ, start_response) -> [body_bytes].
    """
    mcp_modules = {
        module_id: module
        for module_id, module in modules.items()
        if is_mcp_module(module) and get_mcp_config(module).get(MCP_ENABLE_SERVER)
    }

    def wsgi_app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")

        if path in ("/mcp", "/mcp/"):
            body = json.dumps({
                "servers": [
                    {
                        "id": module_id,
                        "name": module["manifest"].get("name", module_id),
                        "tools": server_info(module)["tools"],
                    }
                    for module_id, module in mcp_modules.items()
                ]
            }, ensure_ascii=False)
            status = "200 OK"
            content_type = "application/json"
        elif path.startswith("/mcp/"):
            module_id = path[len("/mcp/"):]
            module = mcp_modules.get(module_id)
            if module is None:
                body = json.dumps({"error": f"Servidor MCP '{module_id}' no encontrado"})
                status = "404 Not Found"
                content_type = "application/json"
            elif method != "POST":
                body = json.dumps({"error": "Método no permitido (use POST)"})
                status = "405 Method Not Allowed"
                content_type = "application/json"
            else:
                try:
                    content_length = int(environ.get("CONTENT_LENGTH", 0) or 0)
                    raw = environ["wsgi.input"].read(content_length) if content_length else b"{}"
                    request_body = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, ValueError):
                    request_body = {}
                response = handle_jsonrpc_request(module, request_body)
                body = json.dumps(response, ensure_ascii=False)
                status = "200 OK"
                content_type = "application/json"
        else:
            body = "Not Found"
            status = "404 Not Found"
            content_type = "text/plain"

        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        start_response(status, [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body_bytes))),
        ])
        return [body_bytes]

    return wsgi_app