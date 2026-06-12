from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import __version__, config
from . import auth, storage


class AzuriomLogin(BaseModel):
    email: str
    password: str
    code: str | None = None


class AzuriomToken(BaseModel):
    access_token: str


app = FastAPI(title="BebraLand Launcher Backend", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/v1/profiles")
def profiles() -> dict[str, list[dict[str, Any]]]:
    return {"profiles": [storage.public_profile(profile) for profile in storage.list_profiles()]}


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
        return {
            "status": "success",
            "provider": "azuriom",
            "user": auth.normalize_azuriom_user(verified),
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
    platform: str = Query("windows"),
) -> dict[str, Any]:
    release = storage.latest_release()
    if not release or release.get("platform", "windows") != platform:
        return {"update_available": False, "current_version": current_version}
    return {
        "update_available": release.get("version") != current_version,
        "current_version": current_version,
        "release": release,
    }
