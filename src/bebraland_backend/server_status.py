from __future__ import annotations

import json
import socket
import struct
import time
from typing import Any


DEFAULT_PORT = 25565
DEFAULT_TIMEOUT_SECONDS = 1.5


def _pack_varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            result.append(byte | 0x80)
        else:
            result.append(byte)
            return bytes(result)


def _read_varint(handle: socket.SocketIO) -> int:
    value = 0
    shift = 0
    for _ in range(5):
        raw = handle.read(1)
        if not raw:
            raise TimeoutError("Minecraft status response ended early")
        byte = raw[0]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value
        shift += 7
    raise ValueError("Minecraft status VarInt is too large")


def _pack_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return _pack_varint(len(data)) + data


def _pack_packet(packet_id: int, payload: bytes = b"") -> bytes:
    packet = _pack_varint(packet_id) + payload
    return _pack_varint(len(packet)) + packet


def _description_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = str(value.get("text") or "")
        extra = value.get("extra")
        if isinstance(extra, list):
            text += "".join(_description_text(item) for item in extra)
        return text
    if isinstance(value, list):
        return "".join(_description_text(item) for item in value)
    return ""


def query_java_server(host: str, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    host = str(host or "").strip()
    if not host:
        raise ValueError("Server host is required")
    port = int(port or DEFAULT_PORT)

    started = time.perf_counter()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        handle = sock.makefile("rb")
        handshake = (
            _pack_varint(-1)
            + _pack_string(host)
            + struct.pack(">H", port)
            + _pack_varint(1)
        )
        sock.sendall(_pack_packet(0, handshake) + _pack_packet(0))

        _read_varint(handle)
        packet_id = _read_varint(handle)
        if packet_id != 0:
            raise ValueError(f"Unexpected Minecraft status packet id: {packet_id}")
        payload_length = _read_varint(handle)
        payload = handle.read(payload_length)
        if not payload or len(payload) != payload_length:
            raise TimeoutError("Minecraft status payload ended early")

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    data = json.loads(payload.decode("utf-8"))
    players = data.get("players") if isinstance(data, dict) else {}
    version = data.get("version") if isinstance(data, dict) else {}
    return {
        "online": True,
        "latency_ms": elapsed_ms,
        "players": {
            "online": int(players.get("online") or 0) if isinstance(players, dict) else 0,
            "max": int(players.get("max") or 0) if isinstance(players, dict) else 0,
        },
        "version": {
            "name": str(version.get("name") or "") if isinstance(version, dict) else "",
            "protocol": int(version.get("protocol") or 0) if isinstance(version, dict) else 0,
        },
        "description": _description_text(data.get("description")) if isinstance(data, dict) else "",
    }


def offline_payload(error: Exception) -> dict[str, Any]:
    return {
        "online": False,
        "latency_ms": None,
        "players": {"online": 0, "max": 0},
        "version": {"name": "", "protocol": 0},
        "description": "",
        "error": str(error),
    }
