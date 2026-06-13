from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__, config
from . import auth, minecraft_profile, storage, yggdrasil


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
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
            self._send_locks[websocket] = asyncio.Lock()

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)
            self._send_locks.pop(websocket, None)

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


def profiles_payload() -> dict[str, list[dict[str, Any]]]:
    return {"profiles": [storage.public_profile(profile) for profile in storage.list_profiles()]}


def launcher_update_payload(current_version: str, platform: str) -> dict[str, Any]:
    release = storage.latest_release(platform)
    if not release:
        return {"update_available": False, "current_version": current_version}
    return {
        "update_available": release.get("version") != current_version,
        "current_version": current_version,
        "release": release,
    }


def storage_signature() -> tuple[tuple[str, int, int], ...]:
    storage.ensure_data_dirs()
    watched = [storage.profiles_file()]
    builds_root = storage.data_dir() / "builds"
    if builds_root.exists():
        watched.extend(build_root / "latest.json" for build_root in builds_root.iterdir() if build_root.is_dir())
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
    payload = await asyncio.to_thread(profiles_payload)
    await manager.broadcast({"type": "profiles.changed", "reason": reason, **payload})


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
def profiles() -> dict[str, list[dict[str, Any]]]:
    return profiles_payload()


@app.get("/api/v1/profiles/{slug}/latest")
def latest(slug: str) -> dict[str, Any]:
    try:
        return storage.build_profile(slug, config.public_base_url())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/profiles/{slug}/builds/{build_id}/manifest")
def manifest(slug: str, build_id: str) -> dict[str, Any]:
    try:
        return storage.manifest_for(slug, build_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/files/{slug}/{build_id}/{file_path:path}")
def file(slug: str, build_id: str, file_path: str) -> FileResponse:
    try:
        return FileResponse(storage.file_for(slug, build_id, file_path))
    except (FileNotFoundError, ValueError) as exc:
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
    platform: str = Query("windows-x64"),
) -> dict[str, Any]:
    return launcher_update_payload(current_version, platform)


async def websocket_result(message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if message_type == "ping":
        return {"status": "ok", "version": __version__}
    if message_type == "profiles.list":
        return await asyncio.to_thread(profiles_payload)
    if message_type == "profile.latest":
        slug = str(payload.get("slug") or "")
        if not slug:
            raise ValueError("slug is required")
        manifest_payload = await asyncio.to_thread(storage.build_profile, slug, config.public_base_url())
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
    if message_type == "launcher.update":
        return await asyncio.to_thread(
            launcher_update_payload,
            str(payload.get("current_version") or "0.0.0"),
            str(payload.get("platform") or "windows-x64"),
        )
    raise ValueError(f"Unknown websocket message type: {message_type}")


def websocket_error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, auth.AzuriomAuthError):
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
        await manager.send(websocket, {"type": "profiles.changed", "reason": "hello", **profiles_payload()})
        while True:
            message = await websocket.receive_json()
            message_id = message.get("id")
            message_type = str(message.get("type") or "")
            payload = message.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            try:
                result = await websocket_result(message_type, payload)
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
