"""Roblox telemetry carried on the existing ACK call. Memory only, no secrets.

Phase 10A: the host controller needs Roblox FPS and RAM. Roblox already POSTs
/v1/events/ack after every processed batch, so the same call carries an optional
telemetry block. Nothing new is opened on the Roblox side.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# name -> (minimum, maximum) accepted numeric range
NUMBER_FIELDS = {
    "fps": (0.0, 500.0),
    "physicsFps": (0.0, 500.0),
    "ramMb": (0.0, 100000.0),
    "players": (0.0, 10000.0),
    "uptime": (0.0, 1_000_000_000.0),
    "placeVersion": (0.0, 1_000_000_000.0),
    "instances": (0.0, 5_000_000.0),
}
TEXT_FIELDS = {"jobId": 64, "placeName": 64}
FRESH_SECONDS = 90.0


def _clean_number(name: str, value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    low, high = NUMBER_FIELDS[name]
    number = float(value)
    if number != number or number < low or number > high:
        return None
    return round(number, 2)


def _clean_text(name: str, value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    limit = TEXT_FIELDS[name]
    if len(value.encode("utf-8")) > limit or any(ord(char) < 32 for char in value):
        return None
    return value


def clean_telemetry(payload: Any) -> dict[str, Any] | None:
    """Drop unknown keys and out-of-range values. An empty result is not stored."""
    if not isinstance(payload, dict):
        return None
    clean: dict[str, Any] = {}
    for name in NUMBER_FIELDS:
        if name in payload:
            number = _clean_number(name, payload[name])
            if number is not None:
                clean[name] = number
    for name in TEXT_FIELDS:
        if name in payload:
            text = _clean_text(name, payload[name])
            if text is not None:
                clean[name] = text
    if not clean:
        return None
    clean["at"] = round(time.time(), 3)
    return clean


class TelemetryStore:
    """Latest Roblox telemetry snapshot for one bridge process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._row: dict[str, Any] | None = None

    def update(self, payload: Any) -> dict[str, Any] | None:
        clean = clean_telemetry(payload)
        if clean is None:
            return None
        with self._lock:
            self._row = clean
        return dict(clean)

    def get(self) -> dict[str, Any]:
        with self._lock:
            row = dict(self._row) if self._row is not None else None
        if row is None:
            return {"ok": False, "telemetry": None, "ageSeconds": None, "fresh": False, "note": "Chưa có telemetry Roblox."}
        age = max(0.0, round(time.time() - float(row.get("at") or 0), 1))
        return {
            "ok": True,
            "telemetry": row,
            "ageSeconds": age,
            "fresh": age <= FRESH_SECONDS,
            "note": "Telemetry Roblox đi kèm ACK thật.",
        }

    def clear(self) -> None:
        with self._lock:
            self._row = None
