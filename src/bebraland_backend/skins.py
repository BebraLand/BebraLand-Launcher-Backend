from __future__ import annotations

import base64
import json
import mimetypes
import uuid
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote, urljoin

from . import auth


MAX_TEXTURE_BYTES = 4 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class SkinApiError(RuntimeError):
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(payload.get("message") or payload.get("reason") or "Skin API request failed")


def skin_api_url(path: str) -> str:
    return urljoin(auth.azuriom_base_url(), path.lstrip("/"))


def _request_json(url: str, request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"status": "error", "message": body or str(exc)}
        raise SkinApiError(exc.code, parsed) from exc
    except urllib.error.URLError as exc:
        raise SkinApiError(
            502,
            {"status": "error", "reason": "skin_api_unreachable", "message": str(exc.reason)},
        ) from exc


def get_json(path: str) -> dict[str, Any]:
    url = skin_api_url(path)
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    return _request_json(url, request)


def profile(username: str) -> dict[str, Any]:
    username = str(username or "").strip()
    if not username:
        raise SkinApiError(400, {"status": "error", "message": "username is required"})
    return get_json(f"api/skin-api/profile/{quote(username, safe='')}")


def avatar_url(kind: str, username: str) -> str:
    kind = str(kind or "").strip().lower()
    if kind not in {"face", "body", "combo"}:
        raise SkinApiError(400, {"status": "error", "message": "invalid avatar kind"})
    username = str(username or "").strip()
    if not username:
        raise SkinApiError(400, {"status": "error", "message": "username is required"})
    return skin_api_url(f"api/skin-api/avatars/{kind}/{quote(username, safe='')}.png")


def profile_payload(username: str) -> dict[str, Any]:
    skin_profile = profile(username)
    return {
        "profile": skin_profile,
        "avatars": {
            "face_url": avatar_url("face", username),
            "body_url": avatar_url("body", username),
            "combo_url": avatar_url("combo", username),
        },
    }


def _validate_png(image: bytes) -> None:
    if not image:
        raise SkinApiError(400, {"status": "error", "message": "image is required"})
    if len(image) > MAX_TEXTURE_BYTES:
        raise SkinApiError(413, {"status": "error", "message": "image is too large"})
    if not image.startswith(PNG_SIGNATURE):
        raise SkinApiError(400, {"status": "error", "message": "image must be PNG"})


def decode_image_base64(value: str) -> bytes:
    try:
        return base64.b64decode(str(value or ""), validate=True)
    except ValueError as exc:
        raise SkinApiError(400, {"status": "error", "message": "invalid image base64"}) from exc


def _multipart_body(
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes, str]],
) -> tuple[bytes, str]:
    boundary = f"----BebraLand{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, (filename, body, content_type) in files.items():
        safe_filename = filename.replace('"', "").replace("\r", "").replace("\n", "") or "texture.png"
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; filename="{safe_filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("ascii")
        )
        chunks.append(body)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), boundary


def upload_texture(
    texture_type: str,
    access_token: str,
    image: bytes,
    filename: str = "texture.png",
) -> dict[str, Any]:
    texture_type = str(texture_type or "").strip().lower()
    if texture_type not in {"skin", "cape"}:
        raise SkinApiError(400, {"status": "error", "message": "texture_type must be skin or cape"})
    access_token = str(access_token or "").strip()
    if not access_token:
        raise SkinApiError(401, {"status": "error", "message": "access_token is required"})
    _validate_png(image)

    field_name = "skin" if texture_type == "skin" else "cape"
    endpoint = "skins" if texture_type == "skin" else "capes"
    content_type = mimetypes.guess_type(filename)[0] or "image/png"
    body, boundary = _multipart_body(
        {"access_token": access_token},
        {field_name: (filename, image, content_type)},
    )
    request = urllib.request.Request(
        skin_api_url(f"api/skin-api/{endpoint}"),
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    result = _request_json(skin_api_url(f"api/skin-api/{endpoint}"), request)
    return result or {"status": "success"}
