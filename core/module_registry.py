"""Registro de módulos de Space Lair.

Escanea la carpeta modules/, valida cada module.json y carga el main.py
de cada módulo usando importlib. También detecta módulos MCP
(Model Context Protocol) para integrarlos como servidores o clientes MCP.
"""

import importlib.util
import json
import logging
import os
from typing import Any, Callable, Optional

from core.logger import get_logger, log
from core.mcp_bridge import MCPError, get_mcp_config

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES_DIR = os.path.join(BASE_DIR, "modules")

# Campos obligatorios en module.json
REQUIRED_MANIFEST_FIELDS = ("id", "name", "description", "type", "capabilities")

# Tipos de módulo válidos
VALID_TYPES = ("tool", "agent", "mcp")


def _load_manifest(module_path: str) -> dict:
    """Carga y valida el module.json de un módulo."""
    manifest_path = os.path.join(module_path, "module.json")
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f"No se encontró module.json en {module_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in manifest]
    if missing:
        raise ValueError(
            f"module.json de {module_path} no tiene los campos obligatorios: {missing}"
        )

    if manifest["type"] not in VALID_TYPES:
        raise ValueError(
            f"Tipo de módulo inválido '{manifest['type']}' en {module_path}. "
            f"Válidos: {VALID_TYPES}"
        )

    if not isinstance(manifest["capabilities"], list) or not manifest["capabilities"]:
        raise ValueError(
            f"'capabilities' debe ser una lista no vacía en {module_path}"
        )

    # Validar configuración MCP si el módulo es tipo MCP
    if manifest["type"] == "mcp":
        try:
            get_mcp_config({"manifest": manifest})
        except ValueError as e:
            raise ValueError(f"Config MCP inválida en {module_path}: {e}")

    return manifest


def _load_execute_function(module_path: str) -> Callable:
    """Carga la función execute(payload) del main.py de un módulo."""
    main_path = os.path.join(module_path, "main.py")
    if not os.path.isfile(main_path):
        raise FileNotFoundError(f"No se encontró main.py en {module_path}")

    spec = importlib.util.spec_from_file_location("module_main", main_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo crear el spec para {main_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "execute") or not callable(module.execute):
        raise AttributeError(f"main.py de {module_path} debe definir execute(payload)")

    return module.execute


def load_modules() -> dict[str, dict[str, Any]]:
    """Escanea modules/ y carga cada carpeta con module.json.

    Retorna: {module_id: {"manifest": {...}, "execute": function, "path": "..."}}
    """
    modules: dict[str, dict[str, Any]] = {}

    if not os.path.isdir(MODULES_DIR):
        return modules

    for entry in sorted(os.listdir(MODULES_DIR)):
        module_path = os.path.join(MODULES_DIR, entry)
        if not os.path.isdir(module_path):
            continue

        try:
            manifest = _load_manifest(module_path)
            module_id = manifest["id"]

            # Si es MCP externo (solo server_url), no necesita main.py
            is_mcp = manifest["type"] == "mcp" or "mcp" in manifest
            if is_mcp:
                mcp_config = get_mcp_config({"manifest": manifest})
                if mcp_config.get("server_url"):
                    modules[module_id] = {
                        "manifest": manifest,
                        "execute": None,
                        "path": module_path,
                        "is_mcp": True,
                        "mcp": mcp_config,
                    }
                    continue

            execute = _load_execute_function(module_path)
            modules[module_id] = {
                "manifest": manifest,
                "execute": execute,
                "path": module_path,
                "is_mcp": is_mcp,
                "mcp": get_mcp_config({"manifest": manifest}) if is_mcp else None,
            }
        except (FileNotFoundError, ValueError, ImportError, AttributeError, json.JSONDecodeError, MCPError) as e:
            # Un módulo con errores no debe romper la carga del resto
            log(
                logger,
                logging.ERROR,
                f"Error cargando módulo '{entry}': {e}",
                module_id=entry,
            )

    return modules


def capabilities_map(modules: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Construye el mapa {capability: [module_id, module_id, ...]}."""
    cap_map: dict[str, list[str]] = {}
    for module_id, module in modules.items():
        for capability in module["manifest"].get("capabilities", []):
            cap_map.setdefault(capability, []).append(module_id)
    return cap_map


def get_module(module_id: str, modules: Optional[dict[str, dict[str, Any]]] = None) -> Optional[dict[str, Any]]:
    """Retorna un módulo por su ID.

    Si no se pasa 'modules', se cargan los módulos automáticamente.
    """
    if modules is None:
        modules = load_modules()
    return modules.get(module_id)


def health_check(module_id: str, modules: Optional[dict[str, dict[str, Any]]] = None) -> dict[str, Any]:
    """Verifica la salud de un módulo (dependencias, API keys, etc.).

    Retorna: {"module_id": ..., "healthy": bool, "checks": {...}, "error": ...}
    """
    module = get_module(module_id, modules)
    if module is None:
        return {
            "module_id": module_id,
            "healthy": False,
            "checks": {},
            "error": f"Módulo '{module_id}' no encontrado",
        }

    manifest = module["manifest"]
    checks: dict[str, Any] = {}

    # Verificar que la función execute existe (o es MCP externo válido)
    is_mcp = module.get("is_mcp", False)
    mcp_config = module.get("mcp") or {}
    if is_mcp and mcp_config.get("server_url"):
        checks["mcp_server_url"] = bool(mcp_config.get("server_url"))
        checks["mcp_transport"] = mcp_config.get("transport") in ("http", "sse")
    else:
        checks["execute"] = callable(module.get("execute"))

    # Verificar configuración del proveedor LLM activo.
    # No depende del provider declarado en module.json, sino del proveedor
    # realmente configurado en el sistema (LLM_PROVIDER).
    active_provider = os.environ.get("LLM_PROVIDER", "ollama")
    if active_provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        checks["anthropic_api_key"] = bool(api_key)
    elif active_provider == "ollama":
        # OLLAMA_BASE_URL es la fuente de verdad; OLLAMA_URL se mantiene como
        # fallback legacy por compatibilidad con configuraciones antiguas.
        base_url = os.environ.get("OLLAMA_BASE_URL") or os.environ.get("OLLAMA_URL")
        checks["ollama_url"] = bool(base_url)

    # Para módulos MCP externos, verificar conectividad al servidor
    if is_mcp and mcp_config.get("server_url"):
        server_url = mcp_config["server_url"]
        try:
            # Probar conexión con una llamada tools/list
            import urllib.error
            import urllib.request

            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
            headers = {"Content-Type": "application/json"}
            headers.update(mcp_config.get("headers", {}) or {})
            data = json.dumps(request).encode("utf-8")
            http_request = urllib.request.Request(
                server_url, data=data, headers=headers, method="POST"
            )
            with urllib.request.urlopen(http_request, timeout=5) as resp:
                resp.read()
            checks["mcp_connectivity"] = True
        except Exception as e:
            checks["mcp_connectivity"] = f"Error: {e}"

    # Si el módulo define health_check(), usarlo
    main_path = os.path.join(module["path"], "main.py")
    if os.path.isfile(main_path):
        spec = importlib.util.spec_from_file_location("module_health", main_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
                if hasattr(mod, "health_check") and callable(mod.health_check):
                    try:
                        module_health = mod.health_check()
                        if isinstance(module_health, dict):
                            checks.update(module_health)
                    except Exception as e:
                        checks["module_health_check"] = f"Error: {e}"
            except Exception as e:
                checks["module_import"] = f"Error: {e}"

    # Un check es healthy si es True, o un string que no es un error.
    # Los strings que empiezan con "Error:" o "error" marcan fallo.
    def _is_healthy_check(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return not value.lower().startswith("error")
        return True  # Otros tipos (dict, list, etc.) no se consideran fallos

    healthy = all(_is_healthy_check(value) for value in checks.values())

    return {
        "module_id": module_id,
        "healthy": healthy,
        "checks": checks,
        "error": None if healthy else "Alguna comprobación de salud falló",
    }


def check_all_health(modules: Optional[dict[str, dict[str, Any]]] = None) -> dict[str, dict[str, Any]]:
    """Ejecuta health_check sobre todos los módulos cargados."""
    if modules is None:
        modules = load_modules()
    return {
        module_id: health_check(module_id, modules)
        for module_id in modules
    }