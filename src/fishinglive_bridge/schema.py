"""Event shape accepted by FishingLive EventRouter."""

from __future__ import annotations

import math
import re
from typing import Any

TYPES = {"comment", "follow", "like", "gift"}
PAID_KEYS = {"productId", "gamePassId", "robux", "price", "sku"}
EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ID_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def _text(value: Any, limit: int, allow_empty: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    if any(ord(char) < 32 for char in value):
        return None
    if len(value.encode("utf-8")) > limit:
        return None
    if value == "" and not allow_empty:
        return None
    return value


def parse_event(payload: Any) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(payload, dict):
        return None, "invalid"
    event_id = _text(payload.get("eventId"), 64)
    if event_id is None or EVENT_ID.fullmatch(event_id) is None:
        return None, "invalid"
    kind = payload.get("type")
    if kind not in TYPES:
        return None, "invalid"
    viewer = payload.get("viewerId")
    if isinstance(viewer, str):
        viewer = viewer.strip()
    viewer = _text(viewer, 64)
    if viewer is None:
        return None, "invalid"
    nickname = payload.get("nickname")
    if not isinstance(nickname, str) or any(ord(char) < 32 for char in nickname):
        return None, "invalid"
    if len(nickname.encode("utf-8")) > 80:
        return None, "invalid"
    timestamp = payload.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return None, "invalid"
    if not math.isfinite(timestamp) or timestamp < 0 or timestamp > 1_000_000_000_000:
        return None, "invalid"
    body = payload.get("payload")
    if not isinstance(body, dict) or len(body) > 8:
        return None, "invalid"
    clean: dict[str, Any] = {}
    for key, value in body.items():
        if not isinstance(key, str) or len(key) > 24 or any(ord(char) < 32 for char in key):
            return None, "invalid"
        if key in PAID_KEYS:
            return None, "paid_reward"
        if isinstance(value, str):
            if any(ord(char) < 32 for char in value) or len(value.encode("utf-8")) > 80:
                return None, "invalid"
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None, "invalid"
        clean[key] = value
    return {
        "eventId": event_id,
        "type": kind,
        "viewerId": viewer,
        "nickname": nickname,
        "timestamp": float(timestamp),
        "payload": clean,
    }, ""


def fit_event_id(raw: str) -> str:
    cleaned = ID_CHARS.sub("", raw)
    if cleaned == "":
        cleaned = "x"
    return cleaned[:64]


def clip_text(value: str, limit: int = 80) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")
