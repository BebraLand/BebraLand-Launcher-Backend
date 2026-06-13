from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any


def _offline_uuid(name: str) -> str:
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest)).hex


def _uuid_hex(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return uuid.UUID(text).hex
    except ValueError:
        compact = re.sub(r"[^0-9a-fA-F]", "", text)
        if len(compact) != 32:
            return None
        try:
            return uuid.UUID(hex=compact).hex
        except ValueError:
            return None


def profile_name_from_user(user: dict[str, Any]) -> str:
    name = str(user.get("display_name") or user.get("username") or "").strip()
    if not name:
        raise ValueError("Azuriom user has no Minecraft username")
    return name


def profile_id_from_user(user: dict[str, Any]) -> str:
    configured_uuid = _uuid_hex(user.get("uuid"))
    if configured_uuid:
        return configured_uuid
    return _offline_uuid(profile_name_from_user(user))


def profile_from_user(user: dict[str, Any], include_properties: bool = False) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "id": profile_id_from_user(user),
        "name": profile_name_from_user(user),
    }
    if include_properties:
        profile["properties"] = []
    return profile
