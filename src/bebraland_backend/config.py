from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def server_host() -> str:
    return os.environ.get("BEBRALAND_HOST", "127.0.0.1")


def server_port() -> int:
    return int(os.environ.get("BEBRALAND_PORT", "8765"))


def public_base_url() -> str:
    fallback = f"http://{server_host()}:{server_port()}"
    return os.environ.get("BEBRALAND_PUBLIC_BASE_URL", fallback).rstrip("/")


def authlib_server_name() -> str:
    return os.environ.get("BEBRALAND_AUTHLIB_SERVER_NAME", "BebraLand")


def skin_domains() -> list[str]:
    value = os.environ.get("BEBRALAND_SKIN_DOMAINS", "")
    return [item.strip() for item in value.split(",") if item.strip()]
