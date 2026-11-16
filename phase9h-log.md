# Phase 9H — durable queue, ACK retry, dedup, ordering, mirror reconnect

Date: 2026-11-16. Scope: make the Render-Free bridge survive a restart, a redeploy and a
30–120 s network loss without losing an unacked event and without processing an event
twice; measure FPS/RAM/queue latency; keep the diorama and gameplay untouched; finish with
Play stopped, HTTP/Bridge off, `approvedForLive=false`, no Publish and no TikTok LIVE.

## 1. What was built

| Layer | File | Guarantee |
| --- | --- | --- |
| Queue journal | `src/fishinglive_bridge/journal.py` (`QueueJournal`) + `queue_store.py` | Pending rows, dedup ids, the ack history and the session id are written to SQLite before an ingest is acknowledged. A restart on the same instance recovers every unacked row and keeps the session, so `BridgePoller` keeps acking and never sees a replay. |
| Ack tracking | `GET /v1/acks?after=<cursor>` (ingest key) | The producer can ask which `eventId`s Roblox confirmed, without a second secret and without exposing the queue. |
| Mirror outbox | `mirror.py` + `OutboxJournal` | Every event is stored before the first POST and deleted only after `/v1/acks` lists it. Network failures keep the rows (bounded retry with backoff), a session change forces a replay, and repeats are idempotent. |
| Dedup across redeploy | `BridgePoller` `resetEpoch` | Roblox clears its dedup set only when the server reports a larger `resetEpoch` (explicit `POST /v1/session/reset`), not merely when a restart changes the session id. A replayed `eventId` is dropped instead of applied twice. |

Why this shape fits Render Free: the Free plan has no persistent disk, so no in-process
store can survive a redeploy or a spin-down. The journal covers the cases Render does
guarantee (same instance, process restart) and the producer outbox covers the rest. No
secret is stored: the journals hold event payloads only, and both keys stay in env vars.

New env vars (all optional, `.env.example` updated): `BRIDGE_JOURNAL_PATH`,
`BRIDGE_OUTBOX_PATH`, `BRIDGE_OUTBOX_TTL`.

## 2. Unit tests (real output)

```
35 passed, 1 deselected
```

`tests/test_bridge.py::DurabilityTests` (8 new tests):

- `test_journal_keeps_queue_and_session_across_restart`: 15 rows recovered, same session,
  `depth=15`, `recovered=15`, next sequence continues at 16, re-publishing a stored
  `eventId` answers `duplicate`.
- `test_ack_history_and_reset_epoch`: cursor advances 0→2, `acks_after(2)` is empty, a
  reset clears the ack history and moves `resetEpoch` to 1, the epoch survives a reopen.
- `test_journal_files_hold_no_secrets`: neither SQLite file contains the ingest or the read
  key bytes.
- `test_durable_mirror_survives_queue_restart`: 5 rows stay in the outbox across a
  queue+mirror restart, Roblox acks them once, outbox drains to 0 with `acked=5`.
- `test_durable_mirror_survives_network_loss`: 40 rows survive a full transport outage
  (`queued=40`, `sent=0`, `failures≥1`), then all 40 are created once and acked once.
- `test_durable_mirror_replays_after_session_change`: a new session triggers
  `sessionChanges≥1`, `replayed≥1`, the rows are reposted and the outbox drains.
- `test_api_reports_durability` + `test_journal_paths_come_from_env`: `/v1/health` exposes
  `durable/resetEpoch/ackCursor`, `/v1/acks` is ingest-key only.

## 3. Deployed check

Render `python-bridge-6v9w.onrender.com` runs **1.3.0** (commit `f12910a`):

```
{"ok":true,"version":"1.3.0","sessionId":"693a6360…","resetEpoch":0,"depth":0,"durable":true,"ackCursor":0}
```

Local bridge startup log (no secret printed):

```
INFO fishinglive_bridge queue journal: queue-journal.sqlite3 (0 recovered rows)
INFO fishinglive_bridge mirror: republishing host-controller events to http://127.0.0.1:8799 (outbox durable)
```

`http://127.0.0.1:8799` is `phase9h-proxy.py`, a loopback reverse proxy to Render that can
cut its own `/v1/events` and `/v1/acks` link. The cut only affects those two paths, so the
health probe stays a separate client link. Roblox polls Render directly and is unaffected.
