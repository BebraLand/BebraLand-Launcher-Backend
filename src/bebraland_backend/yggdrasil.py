from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from . import __version__, auth, config, minecraft_profile, storage


router = APIRouter(prefix="/api/yggdrasil")
_joins: dict[str, dict[str, Any]] = {}
_profiles_by_id: dict[str, dict[str, Any]] = {}
_profiles_by_name: dict[str, str] = {}
_state_lock = threading.RLock()
_key_lock = threading.RLock()
_rsa_key: dict[str, int] | None = None
JOIN_TTL_SECONDS = 30
AUTHLIB_DOWNLOAD_URL = "https://authlib-injector.yushi.moe/"


def _json_error(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "errorMessage": message})


def _no_content() -> Response:
    return Response(status_code=204)


def _normalize_profile_id(value: Any) -> str:
    text = str(value or "").replace("-", "").strip().lower()
    if len(text) != 32:
        return ""
    try:
        return uuid.UUID(hex=text).hex
    except ValueError:
        return ""


def _cache_profile(profile: dict[str, Any]) -> dict[str, Any]:
    profile_id = _normalize_profile_id(profile.get("id"))
    name = str(profile.get("name") or "").strip()
    if not profile_id or not name:
        return profile
    cached = {"id": profile_id, "name": name}
    with _state_lock:
        _profiles_by_id[profile_id] = cached
        _profiles_by_name[name.casefold()] = profile_id
    return cached


def cache_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Make a launcher-authenticated profile available to the session API.

    The client asks this API for its own textures in singleplayer, where the
    multiplayer ``join`` endpoint (which normally fills this cache) is never
    called.
    """
    return _cache_profile(profile)


def _lookup_profile_by_name(name: str) -> dict[str, Any]:
    user = {"display_name": name, "username": name, "uuid": None}
    return _cache_profile(minecraft_profile.profile_from_user(user))


def _user_payload(user: dict[str, Any]) -> dict[str, Any]:
    return {"id": minecraft_profile.profile_id_from_user(user), "properties": []}


def _verify_access_token(access_token: str) -> dict[str, Any]:
    if not access_token:
        raise auth.AzuriomAuthError(403, {"message": "Invalid token.", "reason": "invalid_token"})
    verified = auth.azuriom_verify(access_token)
    user = auth.normalize_azuriom_user(verified)
    _cache_profile(minecraft_profile.profile_from_user(user))
    return user


def _azuriom_get_json(path: str) -> dict[str, Any]:
    url = urljoin(auth.azuriom_base_url(), path.lstrip("/"))
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read().decode("utf-8")
        payload = json.loads(body) if body else {}
    return payload if isinstance(payload, dict) else {}


def _azuriom_skin_profile(username: str) -> dict[str, Any]:
    try:
        return _azuriom_get_json(f"api/skin-api/profile/{quote(username, safe='')}")
    except (auth.AzuriomAuthError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return {}


def _hash_value(value: Any) -> str:
    text = str(value or "").strip()
    if ":" in text:
        text = text.split(":", 1)[1]
    return quote(text, safe="") if text else ""


def _texture_proxy_url(texture_type: str, username: str, texture_hash: Any, fallback_url: str) -> str:
    hash_part = _hash_value(texture_hash)
    if not hash_part:
        return fallback_url
    base = config.public_base_url().rstrip("/")
    return f"{base}/api/yggdrasil/textures/{texture_type}/{quote(username, safe='')}/{hash_part}"


def _texture_payload(profile_id: str, profile_name: str) -> dict[str, Any]:
    skin_profile = _azuriom_skin_profile(profile_name)
    textures: dict[str, Any] = {}
    skin = skin_profile.get("skin")
    if isinstance(skin, dict) and skin.get("url"):
        skin_entry: dict[str, Any] = {
            "url": _texture_proxy_url("skin", profile_name, skin.get("hash"), str(skin["url"])),
        }
        skin_entry["metadata"] = {"model": "slim" if skin.get("slim") else "default"}
        textures["SKIN"] = skin_entry
    cape = skin_profile.get("cape")
    if isinstance(cape, dict) and cape.get("url"):
        textures["CAPE"] = {
            "url": _texture_proxy_url("cape", profile_name, cape.get("hash"), str(cape["url"])),
        }
    return {
        "timestamp": int(time.time() * 1000),
        "profileId": profile_id,
        "profileName": profile_name,
        "textures": textures,
    }


def _profile_response(profile: dict[str, Any], signed: bool) -> dict[str, Any]:
    profile_id = _normalize_profile_id(profile.get("id"))
    name = str(profile.get("name") or "").strip()
    response = {"id": profile_id, "name": name, "properties": []}
    texture_value = base64.b64encode(
        json.dumps(_texture_payload(profile_id, name), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    texture_property = {"name": "textures", "value": texture_value}
    if signed:
        texture_property["signature"] = _rsa_sign_sha1(texture_value.encode("ascii"))
    response["properties"].append(texture_property)
    response["properties"].append({"name": "uploadableTextures", "value": "skin,cape"})
    return response


def _skin_domains() -> list[str]:
    domains: list[str] = []
    urls = [config.public_base_url()]
    azuriom_url = _configured_azuriom_base_url()
    if azuriom_url:
        urls.append(azuriom_url)
    for value in urls:
        host = urlsplit(value).hostname
        if host and host not in domains:
            domains.append(host)
    for host in ("localhost", "127.0.0.1"):
        if host not in domains:
            domains.append(host)
    configured = config.skin_domains()
    for host in configured:
        if host not in domains:
            domains.append(host)
    return domains


def _configured_azuriom_base_url() -> str:
    try:
        return auth.azuriom_base_url()
    except auth.AzuriomAuthError:
        return ""


def api_root_url(base_url: str | None = None) -> str:
    base = (base_url or config.public_base_url()).rstrip("/")
    return f"{base}/api/yggdrasil/"


def metadata_payload() -> dict[str, Any]:
    links: dict[str, str] = {}
    azuriom_url = _configured_azuriom_base_url()
    if azuriom_url:
        links["homepage"] = azuriom_url.rstrip("/")
    return {
        "meta": {
            "serverName": config.authlib_server_name(),
            "implementationName": "BebraLand Launcher Backend",
            "implementationVersion": __version__,
            "links": links,
            "feature.non_email_login": True,
            "feature.username_check": False,
            "feature.enable_profile_key": False,
        },
        "skinDomains": _skin_domains(),
        "signaturePublickey": _public_key_pem(),
    }


def server_config_payload() -> dict[str, Any]:
    api_url = api_root_url()
    return {
        "api_url": api_url,
        "authlib_download_url": AUTHLIB_DOWNLOAD_URL,
        "server_jvm_argument": f"-javaagent:authlib-injector.jar={api_url}",
        "server_properties": {
            "online-mode": "true",
            "enforce-secure-profile": "true",
        },
    }


@router.get("")
@router.get("/")
def metadata() -> dict[str, Any]:
    return metadata_payload()


@router.post("/authserver/authenticate", response_model=None)
def authenticate(payload: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    client_token = str(payload.get("clientToken") or uuid.uuid4().hex)
    if not username or not password:
        return _json_error(403, "ForbiddenOperationException", "Invalid credentials. Invalid username or password.")
    try:
        login = auth.azuriom_login(username, password, payload.get("code"))
        user = login["user"]
    except auth.AzuriomAuthError as exc:
        message = str(exc.payload.get("message") or "Invalid credentials. Invalid username or password.")
        return _json_error(403, "ForbiddenOperationException", message)
    profile = _cache_profile(minecraft_profile.profile_from_user(user))
    response: dict[str, Any] = {
        "accessToken": login["access_token"],
        "clientToken": client_token,
        "availableProfiles": [profile],
        "selectedProfile": profile,
    }
    if payload.get("requestUser"):
        response["user"] = _user_payload(user)
    return response


@router.post("/authserver/refresh", response_model=None)
def refresh(payload: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    access_token = str(payload.get("accessToken") or "")
    try:
        user = _verify_access_token(access_token)
    except auth.AzuriomAuthError:
        return _json_error(403, "ForbiddenOperationException", "Invalid token.")
    profile = _cache_profile(minecraft_profile.profile_from_user(user))
    response: dict[str, Any] = {
        "accessToken": access_token,
        "clientToken": str(payload.get("clientToken") or uuid.uuid4().hex),
        "selectedProfile": profile,
    }
    if payload.get("requestUser"):
        response["user"] = _user_payload(user)
    return response


@router.post("/authserver/validate", response_model=None)
def validate(payload: dict[str, Any]) -> Response | JSONResponse:
    try:
        _verify_access_token(str(payload.get("accessToken") or ""))
    except auth.AzuriomAuthError:
        return _json_error(403, "ForbiddenOperationException", "Invalid token.")
    return _no_content()


@router.post("/authserver/invalidate")
def invalidate(payload: dict[str, Any]) -> Response:
    access_token = str(payload.get("accessToken") or "")
    if access_token:
        try:
            auth.azuriom_logout(access_token)
        except auth.AzuriomAuthError:
            pass
    return _no_content()


@router.post("/authserver/signout")
def signout() -> Response:
    return _no_content()


@router.post("/sessionserver/session/minecraft/join", response_model=None)
def join(payload: dict[str, Any], request: Request) -> Response | JSONResponse:
    access_token = str(payload.get("accessToken") or "")
    selected_profile = _normalize_profile_id(payload.get("selectedProfile"))
    server_id = str(payload.get("serverId") or "")
    if not selected_profile or not server_id:
        return _json_error(403, "ForbiddenOperationException", "Invalid token.")
    try:
        user = _verify_access_token(access_token)
    except auth.AzuriomAuthError:
        return _json_error(403, "ForbiddenOperationException", "Invalid token.")
    profile = _cache_profile(minecraft_profile.profile_from_user(user))
    if selected_profile != profile["id"]:
        return _json_error(403, "ForbiddenOperationException", "Invalid token.")
    with _state_lock:
        _joins[server_id] = {
            "expires_at": time.time() + JOIN_TTL_SECONDS,
            "profile": profile,
            "ip": request.client.host if request.client else None,
        }
    return _no_content()


@router.get("/sessionserver/session/minecraft/hasJoined", response_model=None)
def has_joined(
    username: str = Query(""),
    serverId: str = Query(""),
    ip: str | None = Query(None),
) -> dict[str, Any] | Response:
    now = time.time()
    with _state_lock:
        expired = [key for key, value in _joins.items() if value["expires_at"] < now]
        for key in expired:
            _joins.pop(key, None)
        record = _joins.get(serverId)
    if not record:
        return _no_content()
    profile = record["profile"]
    if str(profile.get("name") or "").casefold() != username.casefold():
        return _no_content()
    if ip and record.get("ip") and record["ip"] != ip:
        return _no_content()
    return _profile_response(profile, signed=True)


@router.get("/sessionserver/session/minecraft/profile/{profile_id}", response_model=None)
def profile(profile_id: str, unsigned: str = Query("true")) -> dict[str, Any] | Response:
    normalized = _normalize_profile_id(profile_id)
    with _state_lock:
        cached = _profiles_by_id.get(normalized)
    if not cached:
        return _no_content()
    signed = str(unsigned).lower() == "false"
    return _profile_response(cached, signed=signed)


@router.post("/api/profiles/minecraft")
def profiles_by_name(names: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name in names[:100]:
        value = str(name or "").strip()
        if value:
            result.append(_lookup_profile_by_name(value))
    return result


@router.get("/textures/{texture_type}/{username}/{texture_hash}")
def texture(texture_type: str, username: str, texture_hash: str) -> Response:
    if texture_type not in {"skin", "cape"}:
        raise HTTPException(status_code=404, detail="Texture type not found")
    skin_profile = _azuriom_skin_profile(username)
    entry = skin_profile.get(texture_type)
    if not isinstance(entry, dict) or not entry.get("url"):
        raise HTTPException(status_code=404, detail="Texture not found")
    expected_hash = _hash_value(entry.get("hash"))
    if expected_hash and expected_hash != quote(texture_hash, safe=""):
        raise HTTPException(status_code=404, detail="Texture hash not found")
    request = urllib.request.Request(str(entry["url"]), headers={"Accept": "image/png"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=body, media_type="image/png")


def _key_path() -> Any:
    return storage.data_dir() / "authlib" / "rsa_key.json"


def _load_rsa_key() -> dict[str, int]:
    global _rsa_key
    with _key_lock:
        if _rsa_key:
            return _rsa_key
        path = _key_path()
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            _rsa_key = {key: int(payload[key]) for key in ("n", "e", "d")}
            return _rsa_key
        key = _generate_rsa_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(key, indent=2), encoding="utf-8")
        _rsa_key = key
        return _rsa_key


def _generate_rsa_key(bits: int = 2048) -> dict[str, int]:
    e = 65537
    half = bits // 2
    while True:
        p = _generate_prime(half, e)
        q = _generate_prime(bits - half, e)
        if p == q:
            continue
        phi = (p - 1) * (q - 1)
        if math.gcd(e, phi) == 1:
            return {"n": p * q, "e": e, "d": pow(e, -1, phi)}


def _generate_prime(bits: int, e: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if math.gcd(candidate - 1, e) == 1 and _is_probable_prime(candidate):
            return candidate


def _is_probable_prime(value: int, rounds: int = 32) -> bool:
    small_primes = (
        3,
        5,
        7,
        11,
        13,
        17,
        19,
        23,
        29,
        31,
        37,
        41,
        43,
        47,
    )
    if value < 2 or value % 2 == 0:
        return value == 2
    for prime in small_primes:
        if value == prime:
            return True
        if value % prime == 0:
            return False
    d = value - 1
    s = 0
    while d % 2 == 0:
        s += 1
        d //= 2
    for _ in range(rounds):
        a = secrets.randbelow(value - 3) + 2
        x = pow(a, d, value)
        if x in {1, value - 1}:
            continue
        for _ in range(s - 1):
            x = pow(x, 2, value)
            if x == value - 1:
                break
        else:
            return False
    return True


def _rsa_sign_sha1(data: bytes) -> str:
    key = _load_rsa_key()
    digest_info = bytes.fromhex("3021300906052b0e03021a05000414") + hashlib.sha1(data).digest()
    size = (key["n"].bit_length() + 7) // 8
    padding_len = size - len(digest_info) - 3
    encoded = b"\x00\x01" + (b"\xff" * padding_len) + b"\x00" + digest_info
    signature = pow(int.from_bytes(encoded, "big"), key["d"], key["n"]).to_bytes(size, "big")
    return base64.b64encode(signature).decode("ascii")


def _public_key_pem() -> str:
    key = _load_rsa_key()
    rsa_public = _der_sequence(_der_integer(key["n"]), _der_integer(key["e"]))
    algorithm = _der_sequence(bytes.fromhex("06092a864886f70d010101"), b"\x05\x00")
    subject_public_key = _der_sequence(algorithm, _der_bit_string(rsa_public))
    encoded = base64.b64encode(subject_public_key).decode("ascii")
    lines = "\n".join(encoded[index : index + 64] for index in range(0, len(encoded), 64))
    return f"-----BEGIN PUBLIC KEY-----\n{lines}\n-----END PUBLIC KEY-----\n"


def _der_len(length: int) -> bytes:
    if length < 128:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _der_integer(value: int) -> bytes:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return b"\x02" + _der_len(len(raw)) + raw


def _der_sequence(*parts: bytes) -> bytes:
    body = b"".join(parts)
    return b"\x30" + _der_len(len(body)) + body


def _der_bit_string(value: bytes) -> bytes:
    body = b"\x00" + value
    return b"\x03" + _der_len(len(body)) + body
