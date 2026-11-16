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

## 4. Scenario A — 60 s mirror link loss (Play on)

Driver: `phase9h-run.py`; evidence `phase9h-out/run2.log`, `phase9h-out/phase9h-results.json`.

| Metric | Value |
| --- | --- |
| published during the outage | 60 (`h-out-…`, 1/s, 60 s) |
| mirror during | `queued=60`, `deferred=110`, `failures=9`, `durable=true`, `lastError="Remote end closed connection without response"` |
| Render depth during | **0** (nothing was written while the link was down) |
| proxy | `dropped=138` requests, `errors=0` |
| after reconnect | drained in **16.3 s**, `queued=0`, `acked=140`, `created=130`, **`duplicates=0`**, `failed=0`, `expired=0` |
| end-to-end queue latency (publish → Roblox ack) | avg **54.8 s**, p95 96.8 s, max 103.9 s — the outage is inside that window |

## 5. Scenario B — producer restart with unacked rows

| Metric | Value |
| --- | --- |
| backlog before kill | 30 unacked rows (`h-rst-…`), link down |
| local bridge | killed, 3 s pause, restarted (journal + outbox reopened) |
| recovered in | **4.0 s** after restart |
| mirror after | `queued=0`, `sent=30`, `acked=170`, `failed=0`, `expired=0` |
| local queue after restart | `depth=170` rows recovered from the journal by the new process |
| Render | session unchanged, `ackCursor=170`, `depth=0` |

The first driver attempt aborted (it asked the proxy for `/v1/health` while every path was
cut, `RemoteDisconnected`). That left ~60 unacked rows in the outbox; the next bridge start
replayed them, so it doubles as an unexpected-crash test.

## 6. Scenario C — real Render redeploy with unacked rows

The 1.3.1 push (`523f47a`) did redeploy, but late (~20 min after the push; the watch below
shows the same session until 13:19). At that moment the outbox still held unacked rows and
Play was running.

| Check | Value |
| --- | --- |
| before | session `693a6360…` / `d1a0b9c5…`, outbox `queued>0`, `resetEpoch` 0 / 1 |
| after | session **`d051e1cd…`**, `resetEpoch=0`, `depth=0`, `durable=true`, `ackCursor=60` |
| mirror after restart | `queued=0`, `acked=260`, `failed=0`, `expired=0`, `lastError=""` |
| Roblox | `BridgeState=polled`, `BridgeAcks=4`, `BridgeRouterSeen=60`, `BridgeDedupResets=0`, `BridgeResetEpoch=0` |
| gameplay | `ReplicatedStorage.FishingLive`: `LikeGen=60`, `LikeCount=8`, `ResultSeq=20`, feed rows for `in000…in029` |

Interpretation: the new instance started empty, the producer replayed the unacked rows, and
Roblox processed 60 unique events (two 30-event batches, `count=4` each → 8 likes per
viewer). `BridgeDedupResets` stayed **0** across the session change, so Roblox kept its
dedup set instead of clearing it on restart. Note: `resetEpoch` went 1 → 0 because the new
instance's journal is empty; the guard only resets on a *larger* epoch, which an explicit
`POST /v1/session/reset` produces (with the journal present).


## 7. Duplicate-delivery probe (queue side)

`POST /v1/events` for `eventId=h-inst-1794809855-000` (already processed and acked):

```
{"results":[{"status":"duplicate","sequence":51}],"sessionId":"d051e1cd…","resetEpoch":0}
```

`LikeGen` 60 → 60, `BridgeRouterSeen` 60 → 60, `LikeCount` 8 → 8: the repeat never reached
Roblox and no gameplay value changed. Roblox's own `eventId` dedup (`EventRouter`) was
verified in 9G and is unchanged; 9H adds the queue-side guarantee and the `resetEpoch`
guard so a restart no longer clears it.

## 8. Performance and cleanup

| Metric | Value |
| --- | --- |
| server heartbeat | **60.3 /s** (avg over 3 s, Play, after the replay) |
| `GetRealPhysicsFPS` | **60.0** |
| server memory | 1394 MB after the replay; 1641 MB with editor overhead at Play start |
| client FPS | **14.7** (Studio window unfocused → engine throttle, same as 9F/9G); viewport **456×675 = 0.6756** (9:16) |
| Humanoids / descendants | 11 / 5339 |
| local bridge RSS | ~54 MB, CPU ≈ 0–1 % during the scenarios |
| mirror publish latency (POST) | avg 506 ms, p95 235 ms, max 777 ms (cold TLS + the outage window) |

Cleanup (verified after Stop Play): `HttpService.HttpEnabled=false`,
`BridgeConfig.BridgeEnabled=false` (source), `BridgeMode="mock"`,
`FishingBaitConfig.approvedForLive=false`, local bridge and proxy stopped (ports 8787/8799
free), `.env` `BRIDGE_MIRROR_URL` restored to `https://python-bridge-6v9w.onrender.com`,
place saved as **version 33** (not published), no TikTok LIVE.

## 9. What is proven and what is not

Proven with Play/HTTP evidence:

1. A 60 s loss of the mirror's upstream link loses nothing: 60 rows published while offline,
   all created exactly once after reconnect, `duplicates=0`, `failed=0`.
2. A producer restart with 30 unacked rows recovers in 4.0 s and delivers all of them.
3. A real Render redeploy (1.3.1, session `d051e1cd…`) with unacked rows: the producer
   replayed them into the empty instance and Roblox processed 60 unique events; the outbox
   drained to 0 with no failures or expiries.
4. A duplicate `eventId` is answered `duplicate` by the queue and never reaches Roblox.
5. Latency and FPS/RAM are measured (§8); the diorama and gameplay values were not touched.

Not proven / limits:

1. **No persistent disk on Render Free.** A redeploy or a spin-down still empties the
   journal; recovery depends on the producer outbox (implemented for the mock mirror) and on
   Roblox's `eventId` dedup. A future live ingest path must keep the same outbox.
2. Render deploys were slow and could not be triggered on demand from this session (no
   Render API access); `POST /v1/session/reset` reproduced an empty instance in
   `phase9h-instance-loss.py`, and the real redeploy arrived later by itself.
3. Client FPS was measured with the Studio window unfocused (14.7 fps throttle); focused FPS
   was not re-sampled in 9H.
4. The local `.env` read key is not the Render read key, so a local pull probe gets 401. That
   is expected (Roblox holds the real read key as a Local Secret).
5. `/host` is still 404 publicly; the HTML page remains loopback-only.

## 10. Files

- Code: `src/fishinglive_bridge/journal.py` (new), `queue_store.py`, `mirror.py`, `api.py`,
  `config.py`, `__main__.py`, `host_state.py`, `__init__.py` (1.3.1).
- Tests: `tests/test_bridge.py` (`DurabilityTests`, 8 new; 35 passed, 1 deselected).
- Roblox: `ServerScriptService.FishingLive.BridgePoller` (`resetEpoch` guard,
  `BridgeResetEpoch`, `BridgeDedupResets`, `BridgeRouterSeen` attributes),
  `BridgeConfig` (`BridgeEnabled` restored to `false`).
- Drivers: `phase9h-proxy.py`, `phase9h-run.py`, `phase9h-instance-loss.py`.
- Evidence: `phase9h-out/run2.log`, `phase9h-out/phase9h-results.json`,
  `phase9h-out/scenario-instance-loss.json`, `phase9h-out/duplicate-probe.json`,
  `phase9h-out/deploy-watch2.txt`, `phase9h-out/pytest.txt`, bridge startup logs.
- Backup: `backups/phase9h/bridge/` (sources, tests, docs), `backups/phase9h/Place1.before-9h.rbxl`;
  the pre-9H Roblox scripts are also recoverable from place version 30.

