from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__, config
from . import auth, minecraft_profile, skins, storage, yggdrasil


class AzuriomLogin(BaseModel):
    email: str
    password: str
    code: str | None = None


class AzuriomToken(BaseModel):
    access_token: str


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._send_locks: dict[WebSocket, asyncio.Lock] = {}
        self._users: dict[WebSocket, dict[str, Any] | None] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
            self._send_locks[websocket] = asyncio.Lock()
            self._users[websocket] = None

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)
            self._send_locks.pop(websocket, None)
            self._users.pop(websocket, None)

    async def set_user(self, websocket: WebSocket, user: dict[str, Any] | None) -> None:
        async with self._lock:
            if websocket in self._clients:
                self._users[websocket] = user

    async def clients_with_users(self) -> list[tuple[WebSocket, dict[str, Any] | None]]:
        async with self._lock:
            return [(client, self._users.get(client)) for client in self._clients]

    async def send(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        async with self._lock:
            send_lock = self._send_locks.get(websocket)
        if not send_lock:
            raise RuntimeError("WebSocket is not connected")
        async with send_lock:
            await websocket.send_json(payload)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        stale: list[WebSocket] = []
        for client in clients:
            try:
                await self.send(client, payload)
            except Exception:
                stale.append(client)
        if stale:
            async with self._lock:
                for client in stale:
                    self._clients.discard(client)
                    self._send_locks.pop(client, None)
                    self._users.pop(client, None)


manager = ConnectionManager()


app = FastAPI(title="BebraLand Launcher Backend", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(yggdrasil.router)


def token_from_authorization(value: str | None) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("bearer "):
        return text[7:].strip()
    return text


def user_for_token(access_token: str | None) -> dict[str, Any] | None:
    try:
        return auth.user_for_token(access_token)
    except auth.AzuriomAuthError:
        return None


def profiles_payload(user: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    return {"profiles": [storage.public_profile(profile) for profile in storage.list_profiles(user)]}


def profiles_hash(profiles: list[dict[str, Any]]) -> str:
    payload = json.dumps(profiles, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def profiles_payload_with_hash(user: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = profiles_payload(user)
    payload["profiles_hash"] = profiles_hash(payload["profiles"])
    return payload


def require_visible_profile(slug: str, user: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = storage.get_profile(slug)
    if not storage.profile_visible_to(profile, user):
        raise KeyError(f"Profile not found: {slug}")
    return profile


def numeric_update_id(value: str) -> tuple[int, ...] | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:[.-]\d+)*", text):
        return tuple(int(part) for part in re.split(r"[.-]", text))
    return None


def release_is_newer(release: dict[str, Any], current_version: str, current_update_id: str = "") -> bool:
    release_id = numeric_update_id(str(release.get("update_id") or ""))
    current_id = numeric_update_id(current_update_id)
    if release_id is not None and current_id is not None:
        width = max(len(release_id), len(current_id))
        release_parts = list(release_id) + [0] * (width - len(release_id))
        current_parts = list(current_id) + [0] * (width - len(current_id))
        return release_parts > current_parts
    return str(release.get("version") or "") != current_version


def launcher_update_payload(current_version: str, platform: str, current_update_id: str = "") -> dict[str, Any]:
    release = storage.latest_release(platform)
    if not release:
        return {
            "update_available": False,
            "current_version": current_version,
            "current_update_id": current_update_id,
        }
    return {
        "update_available": release_is_newer(release, current_version, current_update_id),
        "current_version": current_version,
        "current_update_id": current_update_id,
        "release": release,
    }


def relay_enabled() -> bool:
    relay = config.relay_base_url()
    return bool(relay and relay != config.public_base_url())


def relay_json(path: str, access_token: str | None = None) -> dict[str, Any]:
    base = config.relay_base_url()
    if not base:
        raise RuntimeError("Relay backend is not configured")
    request = Request(f"{base}{path}", headers={"Accept": "application/json"}, method="GET")
    if access_token:
        request.add_header("Authorization", f"Bearer {access_token}")
    with urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Relay backend returned invalid JSON")
    return payload


def latest_manifest_payload(slug: str, access_token: str | None, user: dict[str, Any] | None) -> dict[str, Any]:
    require_visible_profile(slug, user)
    if relay_enabled():
        return relay_json(f"/api/v1/profiles/{quote(slug)}/latest", access_token)
    return storage.build_profile(slug, config.file_base_url())


def manifest_payload(slug: str, build_id: str, access_token: str | None, user: dict[str, Any] | None) -> dict[str, Any]:
    require_visible_profile(slug, user)
    if relay_enabled():
        return relay_json(f"/api/v1/profiles/{quote(slug)}/builds/{quote(build_id)}/manifest", access_token)
    return storage.manifest_for(slug, build_id)


def storage_signature() -> tuple[tuple[str, int, int], ...]:
    storage.ensure_data_dirs()
    watched = [storage.profiles_file()]
    builds_root = storage.data_dir() / "builds"
    if builds_root.exists():
        watched.extend(build_root / "latest.json" for build_root in builds_root.iterdir() if build_root.is_dir())
    assets_root = storage.profile_assets_root()
    if assets_root.exists():
        watched.extend(path for path in assets_root.rglob("*") if path.is_file())
    signature: list[tuple[str, int, int]] = []
    for path in sorted(watched):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        signature.append((path.as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


@app.get("/")
def root() -> JSONResponse:
    return JSONResponse(
        {"status": "ok", "version": __version__},
        headers={"X-Authlib-Injector-API-Location": "/api/yggdrasil/"},
    )


async def broadcast_profiles(reason: str) -> None:
    stale: list[WebSocket] = []
    for websocket, user in await manager.clients_with_users():
        try:
            payload = await asyncio.to_thread(profiles_payload_with_hash, user)
            await manager.send(websocket, {"type": "profiles.changed", "reason": reason, **payload})
        except Exception:
            stale.append(websocket)
    for websocket in stale:
        await manager.disconnect(websocket)


async def watch_storage_changes() -> None:
    previous = await asyncio.to_thread(storage_signature)
    while True:
        await asyncio.sleep(1)
        current = await asyncio.to_thread(storage_signature)
        if current != previous:
            previous = current
            await broadcast_profiles("storage_changed")


@app.on_event("startup")
async def start_realtime_watcher() -> None:
    storage.ensure_data_dirs()
    app.state.realtime_watcher = asyncio.create_task(watch_storage_changes())


@app.on_event("shutdown")
async def stop_realtime_watcher() -> None:
    task = getattr(app.state, "realtime_watcher", None)
    if task:
        task.cancel()


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/v1/authlib/config")
def authlib_config() -> dict[str, Any]:
    return yggdrasil.server_config_payload()


@app.get("/api/v1/profiles")
def profiles(authorization: str | None = Header(None)) -> dict[str, list[dict[str, Any]]]:
    return profiles_payload(user_for_token(token_from_authorization(authorization)))


@app.get("/api/v1/profiles/{slug}/latest")
def latest(slug: str, authorization: str | None = Header(None)) -> dict[str, Any]:
    access_token = token_from_authorization(authorization)
    try:
        return latest_manifest_payload(slug, access_token, user_for_token(access_token))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/profiles/{slug}/builds/{build_id}/manifest")
def manifest(slug: str, build_id: str, authorization: str | None = Header(None)) -> dict[str, Any]:
    access_token = token_from_authorization(authorization)
    try:
        return manifest_payload(slug, build_id, access_token, user_for_token(access_token))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/files/{slug}/{build_id}/{file_path:path}")
def file(slug: str, build_id: str, file_path: str) -> FileResponse:
    try:
        return FileResponse(storage.file_for(slug, build_id, file_path))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/assets/profiles/{slug}/{file_name}")
def profile_asset(slug: str, file_name: str) -> FileResponse:
    try:
        return FileResponse(
            storage.profile_asset_file(slug, file_name),
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/auth/azuriom/login")
def azuriom_login(payload: AzuriomLogin) -> dict[str, Any]:
    try:
        return auth.azuriom_login(payload.email, payload.password, payload.code)
    except auth.AzuriomAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.payload) from exc


@app.post("/api/v1/auth/azuriom/verify")
def azuriom_verify(payload: AzuriomToken) -> dict[str, Any]:
    try:
        verified = auth.azuriom_verify(payload.access_token)
        user = auth.normalize_azuriom_user(verified)
        return {
            "status": "success",
            "provider": "azuriom",
            "user": user,
            "minecraft_profile": minecraft_profile.profile_from_user(user),
            "raw": verified,
        }
    except auth.AzuriomAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.payload) from exc


@app.post("/api/v1/auth/azuriom/logout")
def azuriom_logout(payload: AzuriomToken) -> dict[str, Any]:
    try:
        return auth.azuriom_logout(payload.access_token)
    except auth.AzuriomAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.payload) from exc


@app.get("/api/v1/launcher/update")
def launcher_update(
    current_version: str = Query("0.0.0"),
    current_update_id: str = Query(""),
    platform: str = Query("windows-x64"),
) -> dict[str, Any]:
    return launcher_update_payload(current_version, platform, current_update_id)


async def websocket_result(
    message_type: str,
    payload: dict[str, Any],
    access_token: str | None = None,
    user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if message_type == "ping":
        return {"status": "ok", "version": __version__}
    if message_type == "profiles.list":
        return await asyncio.to_thread(profiles_payload_with_hash, user)
    if message_type == "profiles.check":
        current_hash = str(payload.get("hash") or "")
        profile_payload = await asyncio.to_thread(profiles_payload_with_hash, user)
        if current_hash and current_hash == profile_payload["profiles_hash"]:
            return {"unchanged": True, "profiles_hash": current_hash}
        return {"unchanged": False, **profile_payload}
    if message_type == "profile.latest":
        slug = str(payload.get("slug") or "")
        if not slug:
            raise ValueError("slug is required")
        manifest_payload = await asyncio.to_thread(latest_manifest_payload, slug, access_token, user)
        await broadcast_profiles("profile_built")
        return manifest_payload
    if message_type == "auth.azuriom.login":
        return await asyncio.to_thread(
            auth.azuriom_login,
            str(payload.get("email") or ""),
            str(payload.get("password") or ""),
            payload.get("code"),
        )
    if message_type == "auth.azuriom.verify":
        access_token = str(payload.get("access_token") or "")
        verified = await asyncio.to_thread(auth.azuriom_verify, access_token)
        user = auth.normalize_azuriom_user(verified)
        return {
            "status": "success",
            "provider": "azuriom",
            "user": user,
            "minecraft_profile": minecraft_profile.profile_from_user(user),
            "raw": verified,
        }
    if message_type == "auth.azuriom.logout":
        return await asyncio.to_thread(auth.azuriom_logout, str(payload.get("access_token") or ""))
    if message_type == "skin.profile":
        username = str(payload.get("username") or "")
        if not username:
            raise ValueError("username is required")
        return await asyncio.to_thread(skins.profile_payload, username)
    if message_type in {"skin.upload", "cape.upload"}:
        access_token = str(payload.get("access_token") or "")
        image_base64 = str(payload.get("image_base64") or "")
        filename = str(payload.get("filename") or "texture.png")
        texture_type = "skin" if message_type == "skin.upload" else "cape"
        image = skins.decode_image_base64(image_base64)
        return await asyncio.to_thread(skins.upload_texture, texture_type, access_token, image, filename)
    if message_type == "launcher.update":
        return await asyncio.to_thread(
            launcher_update_payload,
            str(payload.get("current_version") or "0.0.0"),
            str(payload.get("platform") or "windows-x64"),
            str(payload.get("current_update_id") or ""),
        )
    raise ValueError(f"Unknown websocket message type: {message_type}")


def websocket_error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, auth.AzuriomAuthError):
        return {"status_code": exc.status_code, "detail": exc.payload}
    if isinstance(exc, skins.SkinApiError):
        return {"status_code": exc.status_code, "detail": exc.payload}
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return {"status_code": 404, "detail": str(exc)}
    if isinstance(exc, ValueError):
        return {"status_code": 400, "detail": str(exc)}
    return {"status_code": 500, "detail": str(exc)}


@app.websocket("/api/v1/ws")
async def websocket_api(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        await manager.send(websocket, {"type": "hello", "version": __version__})
        while True:
            message = await websocket.receive_json()
            message_id = message.get("id")
            message_type = str(message.get("type") or "")
            payload = message.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            access_token = str(message.get("token") or "")
            user = await asyncio.to_thread(user_for_token, access_token)
            await manager.set_user(websocket, user)
            try:
                result = await websocket_result(message_type, payload, access_token, user)
            except Exception as exc:
                await manager.send(
                    websocket,
                    {
                        "id": message_id,
                        "type": "response",
                        "ok": False,
                        "error": websocket_error_payload(exc),
                    },
                )
            else:
                await manager.send(websocket, {"id": message_id, "type": "response", "ok": True, "result": result})
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except RuntimeError:
        await manager.disconnect(websocket)
