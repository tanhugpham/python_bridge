# PHASE 9E log

Place 131643984728821 / universe 10776877200. Stopped before HTTP/Bridge enable.

## Gate
- MCP Edit connected to Hung1.
- Python bridge not listening on 127.0.0.1:8787.
- cloudflared binary present, process not running, no HTTPS tunnel.
- Play Server GetSecret(FISHINGLIVE_BRIDGE_READ_KEY) missing-or-unreadable. Value not read.
- BridgeEnabled false, BridgeMode mock, stale BridgeBaseUrl leftover from old tunnel hostname.
- HttpEnabled false. Not published. TikTok not connected.

## Action
No HTTP, no BridgeEnabled, no tunnel start, no AUTO DEMO, no gameplay edits. Owner must add Local Secret on this Place in Experience Settings → Security, then restart a new trycloudflare tunnel.

## PHASE 9E readout — 2026-11-16

Place 131643984728821 (Hung1), Play Server readout only. No new Like sent. No TikTok. No gameplay edits.

### Endpoints (real api.py; `/v1/debug/queue` does not exist)

| Probe | Result |
|---|---|
| `GET /v1/debug/queue` | 404 → skipped per instruction |
| `GET /v1/health` | 200 `{"ok":true,"sessionId":"7b387d5b87b74a2e91db454b722ec7d7","depth":0}` (Render cold start, ~65 s) |
| `GET /v1/events?limit=20` with `x-api-key` from local `.env` (value not printed) | 200 `{"events":[],"sessionId":"7b387d5b87b74a2e91db454b722ec7d7"}` |
| `GET /host` | 404 (loopback guard, expected) |
| `127.0.0.1:8787` | not listening, no Python bridge process |
| `match-plastic-troubleshooting-kinase.trycloudflare.com` | DNS not resolvable (stale tunnel) |

Live config in Play: `BridgeEnabled=true`, `BridgeMode=mock`, `BridgeBaseUrl=https://python-bridge-6v9w.onrender.com`, `HttpService.HttpEnabled=true`.

### Roblox evidence

`ServerScriptService.FishingLive` attributes after Play:

- `BridgePolls = 17`
- `BridgeAcks = 0`
- `BridgeState = transport`
- `BridgeRunners = 1`
- `BridgePoller = dormant`

Output: client `LogService` history 0 entries; server history only the readout prints; no script errors.

Code path confirmed from `BridgePoller.Source`: `stats.polls` increments before `GET /v1/events`; a transport failure returns `FAILED` before `Router.ingestBatch`, so `EventRouter` never received rows and `ackAll` never ran.

### Conclusion: FAIL (not PASS)

- ACK never succeeded: `BridgeAcks = 0`, and the bridge queue holds no event to ACK (`events: []`, `depth: 0`).
- The Render process now reports a different `sessionId` (`7b387d5b...`), so its in-memory queue was reset; any earlier eventId is gone and cannot be confirmed as pulled or routed.
- Per the rule "PASS only when ACK truly succeeds", PHASE 9E is not PASS.

### Cleanup (verified)

- Stop Play: done.
- Edit mode: `HttpService.HttpEnabled = false`; `BridgeConfig.BridgeEnabled = false`; `BridgeMode = mock`.
- Re-checked after 6 s and after save: `HTTP_ENABLED=false`, `BridgeEnabled=false`.
- Place saved as version 22 (not published).

Next retry must use a live bridge reachable over HTTPS (local venv + fresh tunnel, or an awake Render), update `BridgeBaseUrl` without reusing stale hosts, then require `BridgeAcks > 0` plus a matched eventId in the bridge log.

## PHASE 9E-FIX investigation — 2026-11-16 (no PASS)

Goal: find the real `RequestAsync` failure behind `BridgeState=transport` (17 polls, 0 ACK).

### Verified evidence (Play Server, no Like sent)

| Probe (Roblox server) | Result |
|---|---|
| `GetSecret("FISHINGLIVE_BRIDGE_READ_KEY")` | type `Secret` |
| `RequestAsync` GET `/v1/events?limit=1` with secret → `python-bridge-6v9w.onrender.com` | throws `This secret cannot be used for a specified domain` |
| Same, without `Accept` header | same error (Accept is irrelevant) |
| Same to `match-plastic-troubleshooting-kinase.trycloudflare.com` (last host) | same error |
| Same to `carry-guide-dog-sheer`, `might-cache-instructional-silly`, `probe-check.trycloudflare.com` | same error |
| Same to `http://127.0.0.1:8787` and `http://localhost:8787` | same error |
| GET `/v1/health` (no secret) from MCP `execute_luau` | `Assistant HTTP permissions are not configured` (MCP gate; game code is not subject to it) |

Render side (from PowerShell, read key from `.env`, value not printed):

- `GET /v1/health` 200 `sessionId=7b387d5b... depth=0`
- `GET /v1/events?limit=20` 200 `events=[]`

In-place poller proof (source flag temporarily true, Play, then reverted):

- `BridgePolls=2`, `BridgeAcks=0`, `BridgeState=transport`
- `BridgeLastError="transport: This secret cannot be used for a specified domain"`

### Root cause

The Studio **Local Secret** `FISHINGLIVE_BRIDGE_READ_KEY` has a domain binding that matches **none** of the reachable hosts (Render, every past trycloudflare host, localhost). The engine rejects the request before DNS/TLS, so `Router.ingestBatch` and `/v1/events/ack` never run. It is not HTTP permission, timeout, HTTP status, `Accept`, or the bridge.

The allow-list cannot be read or written from Luau. The Open Cloud Secrets API manages the **game** store only, and `GET /cloud/v2/universes/10776877200/secrets` returned `{"secrets":[]}`. Local playtests cannot read game secrets, so Open Cloud cannot fix this Local Secret.

### Code change (saved v25)

`BridgePoller` now records failure detail in `stats.lastError` and publishes it as the `FishingLive` attribute `BridgeLastError` (transport/http/bad-json text, 160 chars max). Reason codes (`transport`, `http`, ...) are unchanged. No key value is exposed.

### Verdict: still FAIL (blocked before ACK)

No Like was sent, so the Render queue remains `depth=0`. PASS requires the owner to re-bind the Local Secret, then `BridgePolls>0` **and** `BridgeAcks>0`.

### Owner action required (one-time, Studio UI only)

Studio → File → Experience Settings → Security → Local Secrets → `FISHINGLIVE_BRIDGE_READ_KEY`:

- set the domain to `python-bridge-6v9w.onrender.com` (bare host, no scheme/path), or
- delete and recreate the Local Secret with that domain and the same read key from `.env` (`BRIDGE_READ_KEY`).

Do not use `*`. Then Play again and require `BridgeAcks>0` with a matched eventId.

### Cleanup

Stop Play; `HttpEnabled=false`; `BridgeEnabled=false`; `BridgeMode=mock`; verified after 6 s and after save. Saved place version 25. Not published. No TikTok.

### Retry after owner re-bound the Local Secret (2026-11-16)

Owner reported setting the Local Secret domain to `python-bridge-6v9w.onrender.com`. Retest in the same Studio session:

- Render: `/v1/health` 200 `sessionId=7b387d5b... depth=0`; `/v1/events` 200 `[]`.
- Play with `HttpEnabled=true`, `BridgeEnabled=true`: `BridgePolls=2`, `BridgeAcks=0`, `BridgeState=transport`, `BridgeLastError="transport: This secret cannot be used for a specified domain"`.
- `GET /cloud/v2/universes/10776877200/secrets` still returns `{"secrets":[]}` (so the edit was in Local Secrets, not the game store).
- Conclusion: Studio had not reloaded the Local Secret, or the domain field was not committed. Next attempt requires closing and reopening the place (or restarting Studio), confirming the domain is saved, then re-running Play.
- Cleanup after this attempt: Stop Play, `HttpEnabled=false`, `BridgeEnabled=false`, saved v25 (unchanged). No Like sent. No Publish. No TikTok.

## PHASE 9E PASS — 2026-11-16

Owner re-bound the Local Secret domain to `python-bridge-6v9w.onrender.com` and **reopened the place** (the running Studio session had cached the old secret; the first retry in the same session still failed).

### Evidence

Render (from PowerShell, keys from `.env`, not printed):

- `POST /v1/events` → `status=created`, `sequence=1`, eventId `mock9efix-1794802903650` (type `like`, viewer `viewer9efix`, nickname `MockViewer9EFix`, payload `{count:1}`)
- `depth=1` right after publish

Roblox Play (`HttpEnabled=true`, `BridgeEnabled=true`, `BridgeMode=mock`):

- `BridgePolls=22`, `BridgeAcks=1`, `BridgeState=polled`, `BridgeLastError=""`
- `ReplicatedStorage.FishingLive`: `LikeUser=MockViewer9EFix`, `LikeCount=1`, `LikeNeed=500`, `LikeGen=1` → the exact eventId reached `EventRouter` → `FishingMockHost.SendLikes` → `LikeUnlockManager`.

After ACK (bridge side):

- `GET /v1/health` → `depth=0`
- `GET /v1/events?limit=20` → `events=[]`
- Further polls (`BridgePolls` grew to 34) did not re-deliver; `LikeCount` stayed 1.

ACK is proven: `queue.ack` is the only path that removes pending rows, and depth went `1 → 0` with the event gone from `/v1/events`.

### Cleanup (verified)

Stop Play; `HttpEnabled=false`; `BridgeEnabled=false`; `BridgeMode=mock`; verified after 6 s and after save. Place saved as version 25. Not published. No TikTok LIVE.

### Lesson

Studio caches Local Secrets for the open place; after editing a Local Secret, close and reopen the place before retesting. `BridgeLastError` now makes the engine error visible instead of a bare `transport`.
