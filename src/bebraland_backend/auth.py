from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urljoin

from . import config  # noqa: F401 - loads .env once for auth providers


_tokens: dict[str, dict[str, Any]] = {}


class AzuriomAuthError(RuntimeError):
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.payload = payload
        message = payload.get("message") or payload.get("reason") or "Azuriom auth failed"
        super().__init__(str(message))


def azuriom_base_url() -> str:
    value = os.environ.get("AZURIOM_URL", "").strip()
    if not value:
        raise AzuriomAuthError(
            503,
            {
                "status": "error",
                "reason": "azuriom_not_configured",
                "message": "AZURIOM_URL is not configured",
            },
        )
    return value.rstrip("/") + "/"


def azuriom_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = urljoin(azuriom_base_url(), f"api/auth/{path.lstrip('/')}")
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"status": "error", "message": body or str(exc)}
        raise AzuriomAuthError(exc.code, parsed) from exc
    except urllib.error.URLError as exc:
        raise AzuriomAuthError(
            502,
            {"status": "error", "reason": "azuriom_unreachable", "message": str(exc.reason)},
        ) from exc


def normalize_azuriom_user(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"azuriom:{payload.get('id')}",
        "azuriom_id": payload.get("id"),
        "username": payload.get("username"),
        "display_name": payload.get("username") or "AzuriomUser",
        "uuid": payload.get("uuid"),
        "email_verified": payload.get("email_verified"),
        "role": payload.get("role"),
        "banned": payload.get("banned"),
    }


def azuriom_login(email: str, password: str, code: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"email": email, "password": password}
    if code:
        payload["code"] = code
    result = azuriom_post("authenticate", payload)
    if result.get("status") == "pending":
        return {
            "status": "pending",
            "reason": result.get("reason"),
            "requires2fa": result.get("reason") == "2fa",
            "message": result.get("message", "Two-factor code required"),
        }
    token = result.get("access_token")
    if not token:
        raise AzuriomAuthError(
            502,
            {"status": "error", "reason": "missing_access_token", "message": "Azuriom response has no token"},
        )
    verified = azuriom_verify(token)
    user = normalize_azuriom_user(verified)
    _tokens[token] = {"user": user, "provider": "azuriom", "created_at": time.time()}
    return {
        "status": "success",
        "access_token": token,
        "token_type": "bearer",
        "provider": "azuriom",
        "user": user,
        "raw": verified,
    }


def azuriom_verify(access_token: str) -> dict[str, Any]:
    return azuriom_post("verify", {"access_token": access_token})


def azuriom_logout(access_token: str) -> dict[str, Any]:
    result = azuriom_post("logout", {"access_token": access_token})
    _tokens.pop(access_token, None)
    return result
