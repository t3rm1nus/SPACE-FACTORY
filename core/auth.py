"""Autenticación JWT para Space Lair.

Proporciona generación y verificación de tokens JWT para proteger
las operaciones de aprobación/rechazo de tareas (CLI y API).
"""

import hashlib
import hmac
import json
import os
import time
from functools import wraps
from typing import Any, Callable, Optional

from core.logger import get_logger

logger = get_logger(__name__)

# Clave secreta para firmar tokens (desde env o generada)
_SECRET_KEY = os.getenv("SPACE_LAIR_SECRET", "space-lair-dev-secret-change-me")

# Algoritmo de firma
_ALGORITHM = "HS256"

# Tiempo de expiración por defecto (segundos) - 24 horas
DEFAULT_EXPIRY = int(os.getenv("SPACE_LAIR_TOKEN_EXPIRY", str(24 * 3600)))


def _b64url_encode(data: bytes) -> str:
    """Codifica bytes a base64url sin padding."""
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    """Decodifica base64url sin padding a bytes."""
    import base64

    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(header_b64: str, payload_b64: str) -> str:
    """Firma header+payload con HMAC-SHA256."""
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    signature = hmac.new(
        _SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    return _b64url_encode(signature)


def generate_token(
    subject: str = "operator",
    role: str = "admin",
    expiry: Optional[int] = None,
) -> str:
    """Genera un token JWT firmado.

    Args:
        subject: Identificador del sujeto (usuario/servicio).
        role: Rol del sujeto (admin, operator, etc.).
        expiry: Tiempo de expiración en segundos (default: DEFAULT_EXPIRY).

    Returns:
        Token JWT como string.
    """
    now = int(time.time())
    exp = now + (expiry or DEFAULT_EXPIRY)

    header = {"alg": _ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": exp,
    }

    header_b64 = _b64url_encode(json.dumps(header).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload).encode("utf-8"))
    signature = _sign(header_b64, payload_b64)

    token = f"{header_b64}.{payload_b64}.{signature}"
    logger.debug("Token JWT generado para subject=%s role=%s exp=%s", subject, role, exp)
    return token


def verify_token(token: str) -> Optional[dict]:
    """Valida un token JWT.

    Args:
        token: Token JWT a validar.

    Returns:
        Payload del token si es válido, None si es inválido/expirado.
    """
    if not token:
        return None

    try:
        parts = token.split(".")
        if len(parts) != 3:
            logger.warning("Token JWT con formato inválido")
            return None

        header_b64, payload_b64, signature_b64 = parts

        # Verificar firma
        expected_sig = _sign(header_b64, payload_b64)
        if not hmac.compare_digest(expected_sig, signature_b64):
            logger.warning("Token JWT con firma inválida")
            return None

        # Decodificar payload
        payload_bytes = _b64url_decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))

        # Verificar expiración
        exp = payload.get("exp", 0)
        if exp < int(time.time()):
            logger.warning("Token JWT expirado")
            return None

        return payload

    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Error al verificar token JWT: %s", e)
        return None


def require_auth(roles: Optional[list[str]] = None) -> Callable:
    """Decorator para proteger endpoints Flask con autenticación JWT.

    Args:
        roles: Lista de roles permitidos (None = cualquier rol válido).

    Returns:
        Decorator que valida el token en el header Authorization.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from flask import jsonify, request

            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "Token de autenticación requerido"}), 401

            token = auth_header[7:].strip()
            payload = verify_token(token)

            if payload is None:
                return jsonify({"error": "Token inválido o expirado"}), 401

            if roles and payload.get("role") not in roles:
                return jsonify({"error": "Permisos insuficientes"}), 403

            # Adjuntar payload al request para uso posterior
            request.auth_payload = payload  # type: ignore[attr-defined]
            return func(*args, **kwargs)

        return wrapper

    return decorator