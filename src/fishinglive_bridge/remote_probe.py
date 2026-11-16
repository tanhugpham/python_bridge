"""Read-only probe of the public bridge (Render) from the host controller.

The page is served by this machine, so it can only see its own RAM/queue. Roblox
talks to the public bridge, therefore the page asks that bridge for health and
the Roblox telemetry that arrived with the last real ACK. Nothing is written and
no Roblox call is made.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

PROBE_INTERVAL = 5.0
PROBE_TIMEOUT = 4.0


def _get(url: str, key: str = "", timeout: float = PROBE_TIMEOUT) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if key:
        request.add_header("X-Bridge-Key", key)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed bridge URL
        body = response.read(200_000)
        return int(response.status), json.loads(body.decode("utf-8") or "{}")


class RemoteProbe:
    """Background health/telemetry reader for one public bridge URL."""

    def __init__(self, url: str, key: str = "", interval: float = PROBE_INTERVAL) -> None:
        self.url = (url or "").rstrip("/")
        self.key = key or ""
        self.interval = max(1.0, float(interval))
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = self._off()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.url) and urlsplit(self.url).scheme in {"http", "https"}

    def _off(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "url": self.url,
            "ok": False,
            "status": "OFF",
            "checkedAt": None,
            "ageSeconds": None,
            "latencyMs": None,
            "version": "",
            "sessionId": "",
            "resetEpoch": None,
            "depth": None,
            "ackCursor": None,
            "durable": None,
            "telemetry": None,
            "telemetryAge": None,
            "telemetryFresh": False,
            "error": "",
            "note": "Chưa cấu hình BRIDGE_MIRROR_URL. Trang chỉ thấy bridge cục bộ.",
        }

    def snapshot(self) -> dict[str, Any]:
        if self.enabled and self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="remote-probe", daemon=True)
            self._thread.start()
        with self._lock:
            row = dict(self._snapshot)
        checked = row.get("checkedAt")
        if checked:
            row["ageSeconds"] = max(0.0, round(time.time() - float(checked), 1))
        return row

    def refresh(self) -> dict[str, Any]:
        row = self.probe_once() if self.enabled else self._off()
        with self._lock:
            self._snapshot = row
        return dict(row)

    def probe_once(self) -> dict[str, Any]:
        started = time.perf_counter()
        base = self.url
        row = {**self._off(), "enabled": True, "status": "ERROR", "note": ""}
        try:
            status, health = _get(base + "/v1/health")
            row["latencyMs"] = round((time.perf_counter() - started) * 1000, 1)
            if status != 200 or not isinstance(health, dict):
                raise ValueError("health status %s" % status)
            row.update({
                "ok": True,
                "status": "OK",
                "version": str(health.get("version") or ""),
                "sessionId": str(health.get("sessionId") or ""),
                "resetEpoch": health.get("resetEpoch"),
                "depth": health.get("depth"),
                "ackCursor": health.get("ackCursor"),
                "durable": health.get("durable"),
            })
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError, json.JSONDecodeError) as error:
            row["error"] = type(error).__name__
            row["latencyMs"] = round((time.perf_counter() - started) * 1000, 1)
            row["checkedAt"] = round(time.time(), 3)
            row["note"] = "Không gọi được bridge công khai."
            return row
        try:
            status, payload = _get(base + "/v1/telemetry", self.key)
            if status == 200 and isinstance(payload, dict):
                telemetry = payload.get("telemetry")
                row["telemetry"] = telemetry if isinstance(telemetry, dict) else None
                row["telemetryAge"] = payload.get("ageSeconds")
                row["telemetryFresh"] = bool(payload.get("fresh"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError, json.JSONDecodeError) as error:
            if not row["error"]:
                row["error"] = "telemetry:" + type(error).__name__
        row["checkedAt"] = round(time.time(), 3)
        row["note"] = "Bridge công khai trả lời. Telemetry lấy từ ACK thật của Roblox."
        return row

    def _loop(self) -> None:
        while not self._stop.is_set():
            row = self.probe_once()
            with self._lock:
                self._snapshot = row
            if self._stop.wait(self.interval):
                break

    def close(self) -> None:
        self._stop.set()
