# Phase 11 — LIVE runtime switch

Date: 2026-11-16. Version: **1.4.0**. Status: code + tests done; real TikTok
connection not started yet (needs Render env + the owner's live account).

## What changed

- `tiktok_source.py`: the listener now holds `username`/`session_id`/`target_idc`
  in memory and can be switched without a restart. `status()` exposes
  `username`, `connected`, `lastError`, `switches`, `hasSession`, `updatedAt`
  (never the session value). `apply()` validates the handle, keeps the current
  session when only the username is sent, drops the old client and wakes the
  reconnect loop immediately.
- `api.py`: new authenticated endpoints (ingest key in `X-Bridge-Key`):
  - `GET /v1/tiktok` — state; `live=false` in mock mode.
  - `POST /v1/tiktok/switch` — `{username, sessionId, targetIdc}`; 409 in mock
    mode, 422 for a bad handle or a session without `targetIdc`, 503 when the
    live source is missing. The session is memory-only and never returned.
- `__main__.py`: the `TikTokSource` is created before the app so the API can
  reach it; the lifespan task is unchanged otherwise.
- `host_state.py`: host actions `live_switch_tiktok` and `live_tiktok_status`
  proxy to the public bridge with `BRIDGE_MIRROR_URL` + mirror ingest key.
  They are allowed even when the mock session is stopped. Errors are mapped to
  readable Vietnamese messages (mock bridge, wrong key, Render asleep).
- `host-controller/`: the LIVE panel has `sessionid` + `tt-target-idc` inputs,
  **Đổi kênh trên Render** and **Trạng thái TikTok**; the session field is
  cleared after a successful switch. The panel shows `ĐANG KẾT NỐI` /
  `CHƯA KẾT NỐI`, `@username`, whether a session is loaded, switch count and
  the last error. The page also retries a POST once after a bridge restart
  refreshes the CSRF token, and the HTTPS card reads `KHÔNG DÙNG · RENDER`
  while the mirror is enabled.
- `config.py`: `normalize_username()` is shared by env draft, API and host
  validation.

## Tests

`pytest tests -q` with `.venv` + `PYTHONPATH=src`: **42 collected, all passed**
(TikTokLive present, so `test_installed_tiktoklive_api` ran instead of skipping).
New: `LiveSwitchTests.test_switch_endpoint_is_authenticated_and_live_only`,
`LiveSwitchTests.test_host_proxy_validates_and_needs_a_mirror`.
pytest was installed into `.venv` for this run.

## How to use (owner)

1. Render env: `BRIDGE_MODE=live`, `TIKTOK_USERNAME`, `TIKTOK_SESSION_ID`,
   `TIKTOK_TARGET_IDC`. Keep the two bridge keys unchanged.
2. Wait for the redeploy, confirm `/v1/health` reports `version 1.4.0`.
3. Open `http://127.0.0.1:8787/host` (local mock bridge + mirror must run),
   panel LIVE → enter handle (and session/idc when changing account) → **Đổi
   kênh trên Render** → **Trạng thái TikTok**.
4. Roblox keeps `BridgeBaseUrl = https://python-bridge-6v9w.onrender.com`;
   enable HTTP + `BridgeEnabled` for the test only.

## Not done / risks

- No real TikTok connection was made in this phase; the owner must set the
  session and go live.
- Live mode disables the mirror by design: events reach Roblox only through the
  Render process itself.
- Render Free has no disk and sleeps; the live listener reconnects with
  backoff, and `lastError` shows the last failure class.
- Switching accounts needs that account's `sessionid` + `tt-target-idc`; a
  username-only switch reuses the current session.
