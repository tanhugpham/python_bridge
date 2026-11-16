"""Durable journals for the bridge queue and for the mirror outbox.

Render Free gives every instance an ephemeral filesystem: a journal survives a process
restart on the same instance but not a redeploy and not a spin-down. The journal is
therefore layer one of two:

1. ``QueueJournal`` (this process, on the service Roblox polls): pending rows, dedup
   ids, the ack history and the session id. A restart on the same instance keeps the
   session id, so Roblox never resets its dedup set, and keeps every unacked row.
2. ``OutboxJournal`` (the local bridge that produced the events): the producer keeps a
   row until the queue reports it acked. If the service loses memory, the producer
   replays the unacked rows; the queue drops repeats by eventId and Roblox drops
   repeats by eventId too.

No secret is stored in either journal: rows hold event payloads only, and the keys stay
in environment variables.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

BUSY_TIMEOUT_MS = 4000
ACK_HISTORY = 5000
JOURNAL_NAME = "queue-journal.sqlite3"

QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (event_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS acks (
    ack_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    acked_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS acks_order ON acks (ack_seq);
"""

OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    enqueued_at REAL NOT NULL,
    sent_at REAL,
    sent_session TEXT,
    tries INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS outbox_order ON outbox (row_id);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000.0, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
    return connection



class QueueJournal:
    """Pending rows, dedup ids, ack history and session meta for one queue."""

    def __init__(self, path: Path, dedup_cap: int = 20000, ack_history: int = ACK_HISTORY) -> None:
        self.path = Path(path)
        self.name = self.path.name
        self.dedup_cap = max(64, int(dedup_cap))
        self.ack_history = max(64, int(ack_history))
        self.lock = threading.Lock()
        self.conn = connect(self.path)
        with self.lock:
            self.conn.executescript(QUEUE_SCHEMA)
            self.conn.commit()

    # -- meta -------------------------------------------------------------

    def _meta_get(self, key: str, default: str) -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else str(row[0])

    def _meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def load(self) -> dict[str, Any]:
        """Read the journal once at startup. Rows come back in sequence order."""
        with self.lock:
            session_id = self._meta_get("sessionId", "")
            reset_epoch = int(self._meta_get("resetEpoch", "0") or 0)
            next_sequence = int(self._meta_get("nextSequence", "0") or 0)
            ack_cursor = int(self._meta_get("ackCursor", "0") or 0)
            acked_total = int(self._meta_get("ackedTotal", "0") or 0)
            rows = self.conn.execute("SELECT sequence, payload FROM events ORDER BY sequence").fetchall()
            seen = self.conn.execute("SELECT event_id, sequence FROM seen ORDER BY sequence").fetchall()
        return {
            "sessionId": session_id,
            "resetEpoch": reset_epoch,
            "nextSequence": next_sequence,
            "ackCursor": ack_cursor,
            "ackedTotal": acked_total,
            "pending": [(int(sequence), json.loads(payload)) for sequence, payload in rows],
            "seen": [(str(event_id), int(sequence)) for event_id, sequence in seen],
        }

    def save_session(self, session_id: str, reset_epoch: int, next_sequence: int) -> None:
        with self.lock:
            self._meta_set("sessionId", session_id)
            self._meta_set("resetEpoch", str(int(reset_epoch)))
            self._meta_set("nextSequence", str(int(next_sequence)))
            self.conn.commit()

    # -- rows -------------------------------------------------------------

    def add(self, sequence: int, event: dict[str, Any]) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO events (sequence, event_id, payload, created_at) VALUES (?, ?, ?, ?)",
                (int(sequence), event["eventId"], json.dumps(event, ensure_ascii=False), time.time()),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO seen (event_id, sequence) VALUES (?, ?)",
                (event["eventId"], int(sequence)),
            )
            self._trim_seen_locked()
            self.conn.commit()

    def replace(self, sequence: int, event: dict[str, Any]) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE events SET payload = ? WHERE sequence = ?",
                (json.dumps(event, ensure_ascii=False), int(sequence)),
            )
            self.conn.commit()

    def remove(self, sequences: list[int]) -> None:
        if not sequences:
            return
        with self.lock:
            self.conn.executemany("DELETE FROM events WHERE sequence = ?", [(int(item),) for item in sequences])
            self.conn.commit()

    def ack(self, event_ids: list[str]) -> tuple[int, int]:
        """Record acked event ids and return (acked rows, new ack cursor)."""
        with self.lock:
            if not event_ids:
                return 0, int(self._meta_get("ackCursor", "0") or 0)
            now = time.time()
            self.conn.executemany(
                "INSERT INTO acks (event_id, acked_at) VALUES (?, ?)",
                [(event_id, now) for event_id in event_ids],
            )
            cursor = int(self.conn.execute("SELECT COALESCE(MAX(ack_seq), 0) FROM acks").fetchone()[0])
            self._meta_set("ackCursor", str(cursor))
            total = int(self._meta_get("ackedTotal", "0") or 0) + len(event_ids)
            self._meta_set("ackedTotal", str(total))
            self._trim_acks_locked()
            self.conn.commit()
        return len(event_ids), cursor

    def cursor(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COALESCE(MAX(ack_seq), 0) FROM acks").fetchone()[0])

    def acks_after(self, after: int, limit: int) -> dict[str, Any]:
        """Ack history for the producer: ids newer than ``after``, plus the session."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT ack_seq, event_id FROM acks WHERE ack_seq > ? ORDER BY ack_seq LIMIT ?",
                (int(after), max(1, int(limit))),
            ).fetchall()
            cursor = int(self.conn.execute("SELECT COALESCE(MAX(ack_seq), 0) FROM acks").fetchone()[0])
            session_id = self._meta_get("sessionId", "")
            reset_epoch = int(self._meta_get("resetEpoch", "0") or 0)
            depth = int(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        if rows:
            following = int(rows[-1][0])
        elif cursor < int(after):
            following = cursor
        else:
            following = int(after)
        return {
            "after": int(after),
            "next": following,
            "cursor": cursor,
            "ids": [str(event_id) for _, event_id in rows],
            "sessionId": session_id,
            "resetEpoch": reset_epoch,
            "depth": depth,
        }

    def reset(self, session_id: str, reset_epoch: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM events")
            self.conn.execute("DELETE FROM seen")
            self.conn.execute("DELETE FROM acks")
            self._meta_set("sessionId", session_id)
            self._meta_set("resetEpoch", str(int(reset_epoch)))
            self._meta_set("nextSequence", "1")
            self._meta_set("ackCursor", "0")
            self.conn.commit()

    def counts(self) -> dict[str, int]:
        with self.lock:
            pending = int(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            seen = int(self.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0])
            acks = int(self.conn.execute("SELECT COUNT(*) FROM acks").fetchone()[0])
        return {"pending": pending, "seen": seen, "acks": acks}

    def _trim_seen_locked(self) -> None:
        overflow = int(self.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]) - self.dedup_cap
        if overflow <= 0:
            return
        self.conn.execute(
            "DELETE FROM seen WHERE event_id IN (SELECT event_id FROM seen ORDER BY sequence LIMIT ?)",
            (overflow,),
        )

    def _trim_acks_locked(self) -> None:
        overflow = int(self.conn.execute("SELECT COUNT(*) FROM acks").fetchone()[0]) - self.ack_history
        if overflow <= 0:
            return
        self.conn.execute(
            "DELETE FROM acks WHERE ack_seq IN (SELECT ack_seq FROM acks ORDER BY ack_seq LIMIT ?)",
            (overflow,),
        )

    def close(self) -> None:
        with self.lock:
            self.conn.close()


class OutboxJournal:
    """Producer-side outbox: a row leaves only after the queue reports it acked."""

    def __init__(self, path: Path, capacity: int = 20000) -> None:
        self.path = Path(path)
        self.name = self.path.name
        self.capacity = max(64, int(capacity))
        self.lock = threading.Lock()
        self.conn = connect(self.path)
        with self.lock:
            self.conn.executescript(OUTBOX_SCHEMA)
            self.conn.commit()

    def _meta_get(self, key: str, default: str) -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else str(row[0])

    def _meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def load(self) -> dict[str, Any]:
        with self.lock:
            queued = int(self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])
            cursor = int(self._meta_get("ackCursor", "0") or 0)
            session_id = self._meta_get("sessionId", "")
            acked_total = int(self._meta_get("ackedTotal", "0") or 0)
        return {"queued": queued, "cursor": cursor, "sessionId": session_id, "ackedTotal": acked_total}

    def save_state(self, cursor: int, session_id: str, acked_total: int) -> None:
        with self.lock:
            self._meta_set("ackCursor", str(int(cursor)))
            self._meta_set("sessionId", session_id)
            self._meta_set("ackedTotal", str(int(acked_total)))
            self.conn.commit()

    def add(self, event: dict[str, Any]) -> str:
        """Store one event: "added", "duplicate" (same eventId) or "full"."""
        with self.lock:
            queued = int(self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])
            if queued >= self.capacity:
                return "full"
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO outbox (event_id, payload, enqueued_at) VALUES (?, ?, ?)",
                (event["eventId"], json.dumps(event, ensure_ascii=False), time.time()),
            )
            self.conn.commit()
            return "added" if cursor.rowcount > 0 else "duplicate"

    def ages(self, event_ids: list[str]) -> list[float]:
        """Seconds between enqueue and now, for end-to-end queue latency."""
        if not event_ids:
            return []
        now = time.time()
        with self.lock:
            rows = self.conn.execute(
                "SELECT enqueued_at FROM outbox WHERE event_id IN (%s)" % ",".join("?" * len(event_ids)),
                tuple(event_ids),
            ).fetchall()
        return [max(0.0, now - float(row[0])) for row in rows]


    def take(self, session_id: str, limit: int, older_than: float = 10.0) -> list[dict[str, Any]]:
        """Rows that still need a POST: never sent, sent elsewhere, or sent but unacked."""
        now = time.time()
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT row_id, payload, enqueued_at, tries
                FROM outbox
                WHERE sent_session IS NULL
                   OR sent_session != ?
                   OR sent_at IS NULL
                   OR sent_at <= ?
                ORDER BY row_id
                LIMIT ?
                """,
                (session_id, now - max(0.5, float(older_than)), max(1, int(limit))),
            ).fetchall()
        return [
            {
                "rowId": int(row_id),
                "event": json.loads(payload),
                "enqueuedAt": float(enqueued_at),
                "tries": int(tries),
            }
            for row_id, payload, enqueued_at, tries in rows
        ]

    def mark_sent(self, row_ids: list[int], session_id: str, count: int = 1) -> None:
        if not row_ids:
            return
        with self.lock:
            self.conn.executemany(
                "UPDATE outbox SET sent_at = ?, sent_session = ?, tries = tries + ? WHERE row_id = ?",
                [(time.time(), session_id, int(count), int(row_id)) for row_id in row_ids],
            )
            self.conn.commit()

    def mark_retry(self, row_ids: list[int]) -> None:
        if not row_ids:
            return
        with self.lock:
            self.conn.executemany(
                "UPDATE outbox SET tries = tries + 1 WHERE row_id = ?",
                [(int(row_id),) for row_id in row_ids],
            )
            self.conn.commit()

    def mark_acked(self, event_ids: list[str]) -> int:
        if not event_ids:
            return 0
        with self.lock:
            cursor = self.conn.executemany(
                "DELETE FROM outbox WHERE event_id = ?",
                [(event_id,) for event_id in event_ids],
            )
            self.conn.commit()
        return max(0, int(cursor.rowcount))

    def force_resend(self) -> None:
        """A new queue instance exists: every unacked row must be posted again."""
        with self.lock:
            self.conn.execute("UPDATE outbox SET sent_session = NULL")
            self.conn.commit()

    def pending(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def unsent(self, session_id: str, older_than: float = 10.0) -> int:
        with self.lock:
            return int(self.conn.execute(
                """
                SELECT COUNT(*) FROM outbox
                WHERE sent_session IS NULL OR sent_session != ?
                   OR sent_at IS NULL OR sent_at <= ?
                """,
                (session_id, time.time() - max(0.5, float(older_than))),
            ).fetchone()[0])

    def purge_expired(self, ttl: float) -> int:
        """Bound storage: give up on rows nobody acked within ``ttl`` seconds."""
        if ttl <= 0:
            return 0
        with self.lock:
            cursor = self.conn.execute("DELETE FROM outbox WHERE enqueued_at < ?", (time.time() - ttl,))
            self.conn.commit()
        return max(0, int(cursor.rowcount))

    def close(self) -> None:
        with self.lock:
            self.conn.close()

