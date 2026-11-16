"""Optional upstream mirror for the mock host controller.

The host controller publishes into the in-memory queue of the process that serves it.
That process binds 127.0.0.1, so Roblox cannot poll it: the Local Secret is bound to the
Render service. When BRIDGE_MIRROR_URL is set, every event published locally is copied to
that service's authenticated ingest API, which is the queue Roblox actually polls.

The copy is asynchronous and batched: submit() never blocks the page request or the AUTO
DEMO / stress workers, and one worker posts at most 20 rows per request (the ingest limit).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Callable

MIRROR_BATCH = 20
MIRROR_OUTBOX = 4000
MIRROR_TIMEOUT = 10.0
MIRROR_RETRIES = 2
LATENCY_SAMPLES = 200
MIRRORED_STATUSES = {"created", "coalesced", "duplicate"}


def post_ingest(url: str, key: str, payload: dict[str, Any], timeout: float) -> tuple[int, str]:
    """POST one ingest batch. HTTP errors are returned as (code, body)."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Bridge-Key": key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        return int(error.code), detail



class UpstreamMirror:
    """Bounded outbox plus one worker that republishes to the Render bridge."""

    def __init__(
        self,
        url: str,
        key: str,
        *,
        batch: int = MIRROR_BATCH,
        outbox: int = MIRROR_OUTBOX,
        timeout: float = MIRROR_TIMEOUT,
        post: Callable[[str, str, dict[str, Any], float], tuple[int, str]] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url.rstrip("/") + "/v1/events"
        self.key = key
        self.batch = max(1, min(batch, MIRROR_BATCH))
        self.outbox_limit = max(self.batch, outbox)
        self.timeout = timeout
        self.post = post or post_ingest
        self.now = now
        self.lock = threading.Lock()
        self.outbox: deque[dict[str, Any]] = deque()
        self.latencies: deque[float] = deque(maxlen=LATENCY_SAMPLES)
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.batches = 0
        self.sent = 0
        self.created = 0
        self.coalesced = 0
        self.duplicates = 0
        self.refused = 0
        self.failed = 0
        self.failures = 0
        self.last_error = ""
        self.last_latency: float | None = None
        self.worker = threading.Thread(target=self._run, name="mirror-worker", daemon=True)
        self.worker.start()

    def submit(self, event: dict[str, Any]) -> None:
        with self.lock:
            if len(self.outbox) >= self.outbox_limit:
                self.refused += 1
                self.last_error = "outbox_full"
                return
            self.outbox.append(dict(event))
        self.wake.set()

    def status(self) -> dict[str, Any]:
        with self.lock:
            latencies = list(self.latencies)
            return {
                "enabled": True,
                "url": self.url,
                "queued": len(self.outbox),
                "batches": self.batches,
                "sent": self.sent,
                "created": self.created,
                "coalesced": self.coalesced,
                "duplicates": self.duplicates,
                "refused": self.refused,
                "failed": self.failed,
                "failures": self.failures,
                "lastError": self.last_error,
                "lastLatencyMs": None if self.last_latency is None else round(self.last_latency, 1),
                "latencyMs": self._latency_summary(latencies),
            }

    def wait_idle(self, timeout: float = 5.0) -> bool:
        deadline = self.now() + timeout
        while self.now() < deadline:
            with self.lock:
                if not self.outbox:
                    return True
            time.sleep(0.02)
        return False

    def close(self, timeout: float = 3.0) -> None:
        self.stop.set()
        self.wake.set()
        if self.worker.is_alive():
            self.worker.join(timeout)

    def _latency_summary(self, latencies: list[float]) -> dict[str, float] | None:
        if not latencies:
            return None
        ordered = sorted(latencies)
        index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
        return {
            "avg": round(sum(ordered) / len(ordered), 1),
            "p95": round(ordered[index], 1),
            "max": round(ordered[-1], 1),
        }

    def _take(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = []
            while self.outbox and len(rows) < self.batch:
                rows.append(self.outbox.popleft())
            return rows

    def _run(self) -> None:
        while True:
            self.wake.wait(0.5)
            self.wake.clear()
            if self.stop.is_set() and not self.outbox:
                return
            rows = self._take()
            if rows:
                self._send(rows)

    def _send(self, rows: list[dict[str, Any]]) -> None:
        payload = {"events": rows}
        attempt = 0
        while True:
            attempt += 1
            started = self.now()
            try:
                status, text = self.post(self.url, self.key, payload, self.timeout)
            except Exception as error:  # urllib raises URLError/OSError/timeout
                status, text = 0, str(error)
            elapsed = (self.now() - started) * 1000.0
            if status == 200:
                created, coalesced, duplicates = self._tally(text, len(rows))
                with self.lock:
                    self.batches += 1
                    self.sent += len(rows)
                    self.created += created
                    self.coalesced += coalesced
                    self.duplicates += duplicates
                    self.last_latency = elapsed
                    self.latencies.append(elapsed)
                    self.last_error = ""
                return
            if status in {0, 429, 500, 502, 503, 504} and attempt <= MIRROR_RETRIES:
                self.stop.wait(0.4 * attempt)
                continue
            with self.lock:
                self.failed += len(rows)
                self.failures += 1
                self.last_error = (text or "http_%s" % status)[:160]
            return

    def _tally(self, text: str, fallback: int) -> tuple[int, int, int]:
        """Count created / coalesced / duplicate rows in the ingest reply."""
        try:
            results = json.loads(text).get("results")
        except Exception:
            results = None
        if not isinstance(results, list):
            return fallback, 0, 0
        created = coalesced = duplicates = 0
        for row in results:
            status = row.get("status") if isinstance(row, dict) else None
            if status == "created":
                created += 1
            elif status == "coalesced":
                coalesced += 1
            elif status == "duplicate":
                duplicates += 1
        return created, coalesced, duplicates
