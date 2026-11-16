"""Optional upstream mirror for the mock host controller.

The host controller publishes into the queue of the process that serves it. That
process binds 127.0.0.1, so Roblox cannot poll it: the Local Secret is bound to the
Render service. When BRIDGE_MIRROR_URL is set, every event published locally is copied
to that service's authenticated ingest API, which is the queue Roblox actually polls.

Phase 9H makes the copy durable. With ``outbox_path`` every event is written to a
SQLite outbox before the first POST and stays there until ``GET /v1/acks`` reports that
Roblox confirmed it. A Render restart, a spin-down or a 30-120 s network loss therefore
cannot drop an event: the worker replays whatever is still unacked, and repeated
eventIds are dropped by the queue and by Roblox.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable

from fishinglive_bridge.journal import OutboxJournal

MIRROR_BATCH = 20
MIRROR_OUTBOX = 4000
MIRROR_TIMEOUT = 10.0
MIRROR_RETRIES = 2
MIRROR_PACE = 0.35
MIRROR_RESEND_AFTER = 30.0
MIRROR_RESEND_TRIES = 6
MIRROR_ACK_POLL = 1.0
MIRROR_TTL = 1800.0
LATENCY_SAMPLES = 200
MIRRORED_STATUSES = {"created", "coalesced", "duplicate"}


def _open(request: urllib.request.Request, timeout: float) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        return int(error.code), detail


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
    return _open(request, timeout)


def fetch_acks(url: str, key: str, after: int, timeout: float) -> tuple[int, str]:
    """GET the queue's ack history. Uses the ingest key: the producer owns this view."""
    query = urllib.parse.urlencode({"after": int(after), "limit": 200})
    request = urllib.request.Request(
        url + "?" + query,
        method="GET",
        headers={"Accept": "application/json", "X-Bridge-Key": key},
    )
    return _open(request, timeout)




class UpstreamMirror:
    """Durable outbox plus one worker that republishes to the Render bridge."""

    def __init__(
        self,
        url: str,
        key: str,
        *,
        batch: int = MIRROR_BATCH,
        outbox: int = MIRROR_OUTBOX,
        timeout: float = MIRROR_TIMEOUT,
        post: Callable[[str, str, dict[str, Any], float], tuple[int, str]] | None = None,
        fetch: Callable[[str, str, int, float], tuple[int, str]] | None = None,
        now: Callable[[], float] = time.monotonic,
        outbox_path: Any = None,
        ttl: float = MIRROR_TTL,
        resend_after: float = MIRROR_RESEND_AFTER,
    ) -> None:
        self.base = url.rstrip("/")
        self.url = self.base + "/v1/events"
        self.acks_url = self.base + "/v1/acks"
        self.key = key
        self.batch = max(1, min(batch, MIRROR_BATCH))
        self.outbox_limit = max(self.batch, outbox)
        self.timeout = timeout
        self.post = post or post_ingest
        self.fetch = fetch or fetch_acks
        self.now = now
        self.ttl = max(0.0, float(ttl))
        self.resend_after = max(0.5, float(resend_after))
        self.lock = threading.Lock()
        self.outbox: deque[dict[str, Any]] = deque()
        self.latencies: deque[float] = deque(maxlen=LATENCY_SAMPLES)
        self.ack_latencies: deque[float] = deque(maxlen=LATENCY_SAMPLES)
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.batches = 0
        self.sent = 0
        self.created = 0
        self.coalesced = 0
        self.duplicates = 0
        self.rejected = 0
        self.refused = 0
        self.failed = 0
        self.failures = 0
        self.deferred = 0
        self.expired = 0
        self.replayed = 0
        self.acked = 0
        self.ack_errors = 0
        self.session_changes = 0
        self.last_error = ""
        self.last_latency: float | None = None
        self.last_ack_at = 0.0
        self.cursor = 0
        self.session_id = ""
        self.reset_epoch = 0
        self.retry_at = 0.0
        self.consecutive = 0
        self.last_ack_poll = 0.0
        self.journal: OutboxJournal | None = None
        self.durable = False
        self.outbox_name = ""
        if outbox_path:
            try:
                journal = OutboxJournal(Path(outbox_path), capacity=self.outbox_limit)
                state = journal.load()
            except Exception as error:
                journal = None
                self.last_error = "outbox_unavailable: " + type(error).__name__
            if journal is not None:
                self.journal = journal
                self.durable = True
                self.outbox_name = journal.name
                self.cursor = int(state["cursor"])
                self.session_id = str(state["sessionId"] or "")
                self.acked = int(state["ackedTotal"])
        self.worker = threading.Thread(target=self._run, name="mirror-worker", daemon=True)
        self.worker.start()


    def submit(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict) or not isinstance(event.get("eventId"), str):
            return
        if self.journal is not None:
            state = self.journal.add(event)
            if state != "added":
                with self.lock:
                    self.refused += 1
                    self.last_error = "outbox_" + state
                return
        else:
            with self.lock:
                if len(self.outbox) >= self.outbox_limit:
                    self.refused += 1
                    self.last_error = "outbox_full"
                    return
                self.outbox.append(dict(event))
        self.wake.set()

    def status(self) -> dict[str, Any]:
        journal = self.journal
        queued = journal.pending() if journal is not None else 0
        unsent = journal.unsent(self.session_id, self.resend_after, MIRROR_RESEND_TRIES) if journal is not None else 0
        with self.lock:
            latencies = list(self.latencies)
            ack_latencies = list(self.ack_latencies)
            if journal is None:
                queued = len(self.outbox)
                unsent = queued
            return {
                "enabled": True,
                "url": self.url,
                "durable": self.durable,
                "outbox": self.outbox_name,
                "queued": queued,
                "unsent": unsent,
                "batches": self.batches,
                "sent": self.sent,
                "created": self.created,
                "coalesced": self.coalesced,
                "duplicates": self.duplicates,
                "rejected": self.rejected,
                "refused": self.refused,
                "failed": self.failed,
                "failures": self.failures,
                "deferred": self.deferred,
                "expired": self.expired,
                "replayed": self.replayed,
                "acked": self.acked,
                "ackErrors": self.ack_errors,
                "sessionChanges": self.session_changes,
                "sessionId": self.session_id,
                "resetEpoch": self.reset_epoch,
                "cursor": self.cursor,
                "lastAckAt": self.last_ack_at,
                "lastError": self.last_error,
                "lastLatencyMs": None if self.last_latency is None else round(self.last_latency, 1),
                "latencyMs": self._latency_summary(latencies),
                "ackLatencyMs": self._latency_summary([value * 1000.0 for value in ack_latencies]),
            }

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """True when every row has been posted to the current queue instance."""
        deadline = self.now() + timeout
        while self.now() < deadline:
            if self.journal is not None:
                if self.journal.unsent(self.session_id, self.resend_after, MIRROR_RESEND_TRIES) == 0:
                    return True
            else:
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
        journal = self.journal
        self.journal = None
        if journal is not None:
            try:
                journal.close()
            except Exception:
                pass


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

    def _blocked(self) -> bool:
        return time.time() < self.retry_at

    def _has_work(self) -> bool:
        if self.journal is not None:
            if self._blocked():
                return False
            return self.journal.unsent(self.session_id, self.resend_after, MIRROR_RESEND_TRIES) > 0
        with self.lock:
            return bool(self.outbox)

    def _take(self) -> list[dict[str, Any]]:
        if self.journal is not None:
            if self._blocked():
                return []
            return self.journal.take(self.session_id, self.batch, self.resend_after, MIRROR_RESEND_TRIES)
        with self.lock:
            rows = []
            while self.outbox and len(rows) < self.batch:
                rows.append({"rowId": 0, "event": self.outbox.popleft(), "enqueuedAt": 0.0, "tries": 0})
            return rows

    def _run(self) -> None:
        while True:
            self.wake.wait(0.5)
            self.wake.clear()
            if self.stop.is_set() and not self._has_work():
                return
            rows = self._take()
            if rows:
                self._send(rows)
                if not self.stop.is_set() and self._has_work():
                    # Render accepts 60 ingest rows per second; one 20-row batch per
                    # 0.35 s keeps a burst (500 viewers) under that gate.
                    self.stop.wait(MIRROR_PACE)
            # Ack polling must never starve while the outbox still holds rows: the queue
            # can be replaced (new session) exactly when there is a backlog.
            if self.journal is not None and not self._blocked():
                self._poll_acks()

    def _send(self, rows: list[dict[str, Any]]) -> None:
        events = [row["event"] for row in rows]
        row_ids = [int(row["rowId"]) for row in rows if row.get("rowId")]
        payload = {"events": events}
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
                created, coalesced, duplicates, rejected = self._tally(text, len(events))
                session = self._field(text, "sessionId")
                if isinstance(session, str) and session:
                    self._adopt_session(session, self._field(text, "resetEpoch"))
                with self.lock:
                    self.batches += 1
                    self.sent += len(events)
                    self.created += created
                    self.coalesced += coalesced
                    self.duplicates += duplicates
                    self.rejected += rejected
                    self.last_latency = elapsed
                    self.latencies.append(elapsed)
                    self.last_error = ""
                    self.consecutive = 0
                    self.retry_at = 0.0
                if self.journal is not None:
                    self.journal.mark_sent(row_ids, self.session_id or "unknown")
                    self.journal.save_state(self.cursor, self.session_id, self.acked)
                return
            if status in {0, 429, 500, 502, 503, 504} and attempt <= MIRROR_RETRIES:
                self.stop.wait(0.4 * attempt)
                continue
            with self.lock:
                self.failures += 1
                self.last_error = (text or "http_%s" % status)[:160]
                if self.journal is None:
                    # Memory mode keeps the old contract: the batch is dropped and counted.
                    self.failed += len(events)
                else:
                    # Durable mode keeps the rows; the next attempt happens after a backoff.
                    self.deferred += len(events)
                    self.consecutive += 1
                    self.retry_at = time.time() + min(8.0, 0.25 * (2 ** min(5, self.consecutive)))
            if self.journal is not None:
                self.journal.mark_retry(row_ids)
            return


    def _field(self, text: str, name: str) -> Any:
        try:
            body = json.loads(text)
        except Exception:
            return None
        return body.get(name) if isinstance(body, dict) else None

    def _adopt_session(self, session: str, epoch: Any) -> None:
        """Track the queue instance. A change means its memory (queue, acks) is new."""
        if epoch is not None:
            try:
                self.reset_epoch = int(epoch)
            except (TypeError, ValueError):
                pass
        with self.lock:
            previous = self.session_id
            if previous == session:
                return
            self.session_id = session
            if previous:
                self.session_changes += 1
        if not previous:
            return
        self.cursor = 0
        self.retry_at = 0.0
        self.consecutive = 0
        if self.journal is not None:
            self.journal.force_resend()
        with self.lock:
            self.replayed += 1
        self.wake.set()

    def _poll_acks(self) -> None:
        journal = self.journal
        if journal is None:
            return
        if time.time() - self.last_ack_poll < MIRROR_ACK_POLL:
            return
        self.last_ack_poll = time.time()
        try:
            status, text = self.fetch(self.acks_url, self.key, self.cursor, self.timeout)
        except Exception as error:
            status, text = 0, str(error)
        if status != 200:
            with self.lock:
                self.ack_errors += 1
                self.last_error = (text or "acks_http_%s" % status)[:160]
            return
        try:
            answer = json.loads(text)
        except Exception:
            answer = None
        if not isinstance(answer, dict):
            with self.lock:
                self.ack_errors += 1
                self.last_error = "acks_bad_json"
            return
        session = answer.get("sessionId")
        if isinstance(session, str) and session and session != self.session_id:
            self._adopt_session(session, answer.get("resetEpoch"))
        ids = answer.get("ids")
        ids = [str(item) for item in ids if isinstance(item, str)] if isinstance(ids, list) else []
        if ids:
            ages = journal.ages(ids)
            journal.mark_acked(ids)
            with self.lock:
                self.acked += len(ids)
                self.last_ack_at = time.time()
                if ages:
                    self.ack_latencies.extend(ages)
        following = answer.get("next")
        if isinstance(following, int) and not isinstance(following, bool) and following >= 0:
            self.cursor = following
        expired = journal.purge_expired(self.ttl)
        if expired:
            with self.lock:
                self.expired += expired
        journal.save_state(self.cursor, self.session_id, self.acked)

    def _tally(self, text: str, fallback: int) -> tuple[int, int, int, int]:
        """Count created / coalesced / duplicate / rejected rows in the ingest reply."""
        try:
            results = json.loads(text).get("results")
        except Exception:
            results = None
        if not isinstance(results, list):
            return fallback, 0, 0, 0
        created = coalesced = duplicates = rejected = 0
        for row in results:
            status = row.get("status") if isinstance(row, dict) else None
            if status == "created":
                created += 1
            elif status == "coalesced":
                coalesced += 1
            elif status == "duplicate":
                duplicates += 1
            else:
                rejected += 1
        return created, coalesced, duplicates, rejected
