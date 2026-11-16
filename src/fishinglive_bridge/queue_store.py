"""Session queue. Unacked events stay until Roblox confirms them.

With ``journal_path`` the queue is durable: pending rows, the dedup ids, the ack
history and the session id survive a process restart, so Roblox keeps acknowledging
the same session and never sees a replayed event twice. Without it the queue stays in
memory, which is what the unit tests for the wire format use.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable

from fishinglive_bridge.journal import QueueJournal
from fishinglive_bridge.schema import parse_event

logger = logging.getLogger("fishinglive_bridge.queue")

JOIN_TEXT = {"cau", "câu"}
LIKE_MERGE_CAP = 1000


class RateGate:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.hits: deque[float] = deque()

    def allow(self, now: float) -> bool:
        while self.hits and now - self.hits[0] >= self.window_seconds:
            self.hits.popleft()
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(now)
        return True


class EventQueue:
    def __init__(
        self,
        path: Any = None,
        now: Callable[[], float] | None = None,
        dedup_cap: int = 20000,
        capacity: int = 2000,
        journal_path: Any = None,
    ) -> None:
        # path is accepted so older callers keep working. The session is not written to disk.
        del path
        self.now = now or __import__("time").time
        self.dedup_cap = dedup_cap
        self.capacity = capacity if capacity >= 1 else 2000
        self.lock = threading.Lock()
        self.pending: dict[int, dict[str, Any]] = {}
        self.seen: dict[str, int] = {}
        self.next_sequence = 1
        self.session_id = uuid.uuid4().hex
        self.coalesced = 0
        self.drops = 0
        self.accepted = 0
        self.dropped = 0
        self.duplicates = 0
        self.pull_count = 0
        self.ack_count = 0
        self.last_pull_at = 0.0
        self.last_ack_at = 0.0
        self.pulled: set[int] = set()
        self.publish_at: dict[int, float] = {}
        self.last_batch_latency_ms: float | None = None
        self.ingest_gate = RateGate(60, 1.0)
        self.pull_gate = RateGate(5, 1.0)
        self.ack_gate = RateGate(5, 1.0)
        self.journal: QueueJournal | None = None
        self.durable = False
        self.recovered = 0
        self.acked = 0
        self.ack_cursor = 0
        self.reset_epoch = 0
        if journal_path:
            self._open_journal(Path(journal_path))

    def _open_journal(self, path: Path) -> None:
        """Recover the last session. A journal that cannot be opened leaves RAM only."""
        try:
            journal = QueueJournal(path, dedup_cap=self.dedup_cap)
            state = journal.load()
        except Exception as error:
            logger.warning("queue journal disabled: %s", type(error).__name__)
            return
        with self.lock:
            if state["sessionId"]:
                self.session_id = state["sessionId"]
            self.reset_epoch = int(state["resetEpoch"])
            self.ack_cursor = int(state["ackCursor"])
            self.acked = int(state["ackedTotal"])
            self.next_sequence = max(1, int(state["nextSequence"]))
            started = self.now()
            for sequence, event in state["pending"]:
                self.pending[sequence] = {"sequence": sequence, "event": event}
                self.publish_at[sequence] = started
                self.recovered += 1
            for event_id, sequence in state["seen"]:
                self.seen[event_id] = sequence
            highest = max(self.pending) if self.pending else 0
            if highest >= self.next_sequence:
                self.next_sequence = highest + 1
            self.journal = journal
            self.durable = True
            journal.save_session(self.session_id, self.reset_epoch, self.next_sequence)

    def _journal_add_locked(self, sequence: int, event: dict[str, Any]) -> None:
        if self.journal is None:
            return
        try:
            self.journal.add(sequence, event)
        except Exception as error:
            self._journal_failed_locked(error)

    def _journal_failed_locked(self, error: Exception) -> None:
        # A full or read-only disk must not stop the broadcast; it only loses durability.
        logger.warning("queue journal write failed (%s); continuing in memory", type(error).__name__)
        self.durable = False
        journal = self.journal
        self.journal = None
        if journal is not None:
            try:
                journal.close()
            except Exception:
                pass


    def publish(self, payload: dict[str, Any], rate_limit: bool = True) -> tuple[str, int | None]:
        event, reason = parse_event(payload)
        if event is None:
            return reason, None
        with self.lock:
            if rate_limit and not self.ingest_gate.allow(self.now()):
                self.dropped += 1
                return "rate_limited", None
            existing = self.seen.get(event["eventId"])
            if existing is not None:
                self.duplicates += 1
                return "duplicate", existing
            if len(self.pending) >= self.capacity:
                self._coalesce_likes_locked()
            if len(self.pending) >= self.capacity and event["type"] == "like":
                folded = self._fold_like_locked(event)
                if folded is not None:
                    self.seen[event["eventId"]] = folded
                    self._trim_seen_locked()
                    self.accepted += 1
                    return "coalesced", folded
            if len(self.pending) >= self.capacity and not _protected(event):
                self.dropped += 1
                return "overloaded", None
            sequence = self.next_sequence
            self.next_sequence += 1
            self.pending[sequence] = {"sequence": sequence, "event": event}
            self.seen[event["eventId"]] = sequence
            self.publish_at[sequence] = self.now()
            self._journal_add_locked(sequence, event)
            self._trim_seen_locked()
            self.accepted += 1
            return "created", sequence

    def pull(self, limit: int) -> tuple[str, list[dict[str, Any]]]:
        if limit < 1:
            limit = 1
        if limit > 20:
            limit = 20
        with self.lock:
            if not self.pull_gate.allow(self.now()):
                return "rate_limited", []
            now = self.now()
            self.last_pull_at = now
            self.pull_count += 1
            rows = [self.pending[key] for key in sorted(self.pending)]
            chosen = rows[:limit]
            latencies = []
            for row in chosen:
                sequence = row["sequence"]
                self.pulled.add(sequence)
                started = self.publish_at.get(sequence)
                if started is not None:
                    latencies.append((now - started) * 1000)
            if latencies:
                self.last_batch_latency_ms = round(max(latencies), 3)
            return "ok", chosen

    def ack(self, sequences: list[int]) -> tuple[str, int]:
        with self.lock:
            if not self.ack_gate.allow(self.now()):
                return "rate_limited", 0
            removed = 0
            acked_ids: list[str] = []
            for sequence in sequences:
                row = self.pending.get(sequence)
                if row is not None:
                    acked_ids.append(row["event"]["eventId"])
                    del self.pending[sequence]
                    self.pulled.discard(sequence)
                    self.publish_at.pop(sequence, None)
                    removed += 1
            if removed:
                self.last_ack_at = self.now()
                self.ack_count += removed
                self.acked += removed
                if self.journal is not None:
                    try:
                        self.journal.ack(acked_ids)
                        self.ack_cursor = self.journal.cursor()
                    except Exception as error:
                        self._journal_failed_locked(error)
            return "ok", removed

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "sessionId": self.session_id,
                "depth": len(self.pending),
                "coalesced": self.coalesced,
                "capacity": self.capacity,
                "accepted": self.accepted,
                "dropped": self.dropped,
                "duplicates": self.duplicates,
                "lastPullAt": self.last_pull_at,
                "lastAckAt": self.last_ack_at,
                "pulls": self.pull_count,
                "acks": self.ack_count,
                "batchLatencyMs": self.last_batch_latency_ms,
                "durable": self.durable,
                "resetEpoch": self.reset_epoch,
                "acked": self.acked,
                "ackCursor": self.ack_cursor,
                "recovered": self.recovered,
                "journal": self.journal.name if self.journal is not None else "",
            }

    def reset(self) -> str:
        with self.lock:
            self.pending.clear()
            self.seen.clear()
            self.next_sequence = 1
            self.coalesced = 0
            self.accepted = 0
            self.dropped = 0
            self.duplicates = 0
            self.pull_count = 0
            self.ack_count = 0
            self.last_pull_at = 0.0
            self.last_ack_at = 0.0
            self.pulled.clear()
            self.publish_at.clear()
            self.last_batch_latency_ms = None
            self.session_id = uuid.uuid4().hex
            self.reset_epoch += 1
            self.acked = 0
            self.ack_cursor = 0
            self.recovered = 0
            if self.journal is not None:
                try:
                    self.journal.reset(self.session_id, self.reset_epoch)
                except Exception as error:
                    self._journal_failed_locked(error)
            return self.session_id

    def note_transport_drop(self) -> dict[str, Any]:
        """A dropped TikTok socket keeps the session, the queue, and the scores."""
        with self.lock:
            self.drops += 1
            return {
                "sessionId": self.session_id,
                "depth": len(self.pending),
                "drops": self.drops,
            }

    def _like_count(self, event: dict[str, Any]) -> int:
        count = event["payload"].get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            return 1
        return count

    def _coalesce_likes_locked(self) -> int:
        groups: dict[str, list[int]] = {}
        for sequence, row in self.pending.items():
            event = row["event"]
            if event["type"] == "like":
                groups.setdefault(event["viewerId"], []).append(sequence)
        removed = 0
        touched: list[int] = []
        dropped: list[int] = []
        for sequences in groups.values():
            if len(sequences) < 2:
                continue
            sequences.sort()
            total = 0
            newest = 0.0
            for sequence in sequences:
                event = self.pending[sequence]["event"]
                total += self._like_count(event)
                newest = max(newest, float(event["timestamp"]))
            if total > LIKE_MERGE_CAP:
                total = LIKE_MERGE_CAP
            kept = self.pending[sequences[0]]["event"]
            kept["payload"] = {"count": total, "merged": True}
            kept["timestamp"] = newest
            touched.append(sequences[0])
            for sequence in sequences[1:]:
                del self.pending[sequence]
                dropped.append(sequence)
                removed += 1
        self.coalesced += removed
        if touched:
            self._journal_sync_locked(touched, dropped)
        return removed

    def _fold_like_locked(self, event: dict[str, Any]) -> int | None:
        target = None
        for sequence in sorted(self.pending):
            row = self.pending[sequence]["event"]
            if row["type"] == "like" and row["viewerId"] == event["viewerId"]:
                target = sequence
                break
        if target is None:
            return None
        kept = self.pending[target]["event"]
        total = self._like_count(kept) + self._like_count(event)
        if total > LIKE_MERGE_CAP:
            total = LIKE_MERGE_CAP
        kept["payload"] = {"count": total, "merged": True}
        if float(event["timestamp"]) > float(kept["timestamp"]):
            kept["timestamp"] = float(event["timestamp"])
        self.coalesced += 1
        self._journal_sync_locked([target], [])
        return target

    def _journal_sync_locked(self, updated: list[int], removed: list[int]) -> None:
        if self.journal is None:
            return
        try:
            for sequence in updated:
                row = self.pending.get(sequence)
                if row is not None:
                    self.journal.replace(sequence, row["event"])
            self.journal.remove(removed)
        except Exception as error:
            self._journal_failed_locked(error)

    def _trim_seen_locked(self) -> None:
        overflow = len(self.seen) - self.dedup_cap
        if overflow <= 0:
            return
        pending_ids = {row["event"]["eventId"] for row in self.pending.values()}
        for event_id in list(self.seen):
            if overflow <= 0:
                break
            if event_id in pending_ids:
                continue
            self.seen.pop(event_id, None)
            overflow -= 1


def _protected(event: dict[str, Any]) -> bool:
    if event["type"] == "gift":
        return True
    if event["type"] != "comment":
        return False
    text = event["payload"].get("text", "")
    if not isinstance(text, str):
        return False
    return " ".join(text.strip().lower().split()) in JOIN_TEXT
