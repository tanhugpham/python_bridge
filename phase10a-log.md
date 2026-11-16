# Phase 10A — Control Center: host page drives every backend endpoint

Date: 2026-11-16. Scope: make the Vietnamese host page (`/host`) the single control surface
for the mock bridge — every backend host action has a button, every panel has real data or
says "Chưa có dữ liệu", nothing in the browser fakes a Roblox result, and the page never
enables LIVE. Finish with the suite green, docs updated, no Publish, no TikTok LIVE.

## 1. What was wired

| Panel | Control | Endpoint / action | Behaviour |
| --- | --- | --- | --- |
| Người xem | Chọn một người xem | `GET /host/viewers` | Row selection drives `selected`, used by like / comment / gift / follow / `/vitri`. `send-follow` is refused client-side until a viewer is selected. |
| Người xem | Tạo ngư dân mẫu | `POST /host/action {action:"seed_fishers", count}` | Count 1–10 (input `min=1 max=10`); each seeded fisher sends like 500 + comment `câu`, so the queue is checked before every fisher. |
| Người xem | Chạy AUTO DEMO | `POST /host/action {action:"auto_demo_start", viewers}` | Viewer count comes from a `<select>` with the only three sizes the backend accepts: 10 / 100 / 500. Any other value is rejected by `host_state` with "AUTO DEMO chỉ nhận 10, 100 hoặc 500 viewer." |
| Người xem | Dừng AUTO DEMO | `auto_demo_stop` | Feedback line shows wave, sent, step (live) or sent/viewer count (stopped). |
| Telemetry | Làm mới / Hiển thị | `GET /host/telemetry` | Shows bridge depth, Roblox ACK cursor, telemetry age and freshness. Missing Roblox telemetry stays empty instead of being invented. |
| Report | Chạy bài test | `run_test_suite` | 20 report rows; `mockOnly` is true in mock mode; `PASS`/`FAIL` only measure the mock bridge path, Render and Roblox stay `WARN` until they answer. |
| Report | Xuất JSON | in-page `Blob` download | Uses the last report already returned; no extra endpoint, no new secret. |
| Report | Xuất CSV | in-page `Blob` download | `csvCell` escapes quotes/comma/newline; UTF-8 BOM for Excel. |
| Live | Lưu bản nháp | `save_live_settings` | Writes `{tiktokUsername, mode, bridgeUrl, updatedAt}` to `BRIDGE_SETTINGS_PATH` (default `data/live-settings.json`); returns `applied=false` always, `liveBlocked=true` when the draft says LIVE. |
| Live | Tải / Làm mới | `GET /host/live-settings` | Draft, statuses, `liveAllowed=false`, `draftPath`, `applyNote`, `savedSettingsEnabled=false`. |

Panel switching now loads the panel's own data (`loadTelemetry`, `loadReport`, `loadLive`),
so a tab opened after start is not stale. Live settings form fields mark `liveDirty` on
`input`/`change` so an unsaved draft is visible before reload overwrites it.

## 2. Honest gates kept

- LIVE stays OFF by design: `save_live_settings` never applies anything (`applied=false`),
  the draft is read only when the bridge is started with `BRIDGE_SAVED_SETTINGS=1`, and
  `liveAllowed` is hard-coded `false`. Other host actions return **409** in live mode.
- `save_live_settings` reads only the explicit `tiktokUsername` field — fixed this phase, so
  a selected viewer (which the page sends as `username`) can no longer leak into the draft.
- Bridge status and Roblox status are separate columns everywhere: `ACCEPTED_BY_BRIDGE`
  never means Roblox processed the event; only `POST /v1/events/ack` yields `ACKNOWLEDGED`.
- No secret reaches the browser: no `localStorage.setItem`, no `BRIDGE_INGEST_KEY=`, the
  page reads only `GET /host/session` (CSRF token + mode).
- `/host` still 404s for tunnel headers, public hosts and non-loopback clients; host actions
  still need the `fl_host_csrf` cookie, `X-CSRF-Token`, a loopback `Host` header and a
  non-cross-site `Origin`/`Sec-Fetch-Site`.

## 3. Verification

| Check | Result |
| --- | --- |
| `pytest tests -q` (`.venv313`, `PYTHONPATH=src`) | **39 passed, 1 skipped** in 12.0 s. The skip is `test_installed_tiktoklive_api` (`@unittest.skipUnless(HAS_TIKTOKLIVE)` — TikTokLive is not installed in this venv), so the suite no longer fails on a machine without it. |
| New tests (4) | `test_ack_carries_telemetry_for_host_page`, `test_live_settings_draft_never_enables_live`, `test_host_test_suite_report_is_honest`, `test_auto_demo_sizes_and_live_gate`. |
| Probe: page ↔ backend | Every action above was driven through `TestClient` with a loopback `client=("127.0.0.1", 50000)`, `Host: 127.0.0.1:8787` and the CSRF pair from `/host/session`, asserting `status=200` **and** `ok=true` for each: `/host/dashboard`, `/host/telemetry`, `/host/report`, `/host/live-settings`, `/host/viewers`, `/host/auto-demo`, `seed_fishers` (3), `auto_demo_start` (10), `auto_demo_stop`, `save_live_settings`, `run_test_suite`, `remote_refresh` — 12/12. Report: 20 rows, `mockOnly=True`, keys `bridgeStatus/detail/eventId/id/name/robloxStatus/status`. `save_live_settings`: `applied=false`, `liveBlocked=false`; the draft read back as `{tiktokUsername: "host_user", mode: "mock"}` because an empty `bridgeUrl` is dropped by `read_live_settings`. |
| Probe: UI ↔ HTML | 109 JS-referenced ids, **0 missing** in `index.html`; `status-row`, `status-list`, `table-wrap` styled in `styles.css`; no `localStorage.setItem`; no `BRIDGE_INGEST_KEY=`. The remaining "unused" ids are section/panel ids used by CSS, not JS. |
| `node --check host-controller/app.js` | clean. |

## 4. Files changed

- `host-controller/app.js` (+245) — listeners for seed / follow / auto demo / report /
  export / telemetry / live settings, panel-driven loads, `latestReport`, `download`,
  `csvCell`.
- `host-controller/index.html` (+82) — Telemetry / Report / Live panels; AUTO DEMO viewer
  count is now a 10 / 100 / 500 `<select>` matching the backend contract.
- `host-controller/styles.css` (+24) — `status-row`, `status-list`, `table-wrap` so the new
  tables and status lists lay out on a narrow window.
- `src/fishinglive_bridge/telemetry.py` (new) — Roblox telemetry carried on the existing
  `/v1/events/ack` call; numeric ranges clamped, unknown keys dropped, memory only.
- `src/fishinglive_bridge/remote_probe.py` (new) — read-only `GET /v1/health` +
  `/v1/telemetry` probe of the public (Render) bridge for the host page.
- `src/fishinglive_bridge/api.py` (+56) — `GET /host/telemetry`, `/host/report`,
  `/host/live-settings`; `/v1/telemetry` behind `x-api-key`.
- `src/fishinglive_bridge/config.py` (+46) — `live_settings_path`
  (`BRIDGE_SETTINGS_PATH`), `read_live_settings`, `BRIDGE_SAVED_SETTINGS` opt-in,
  `USERNAME_CHARS`.
- `src/fishinglive_bridge/host_state.py` (+482) — telemetry snapshot, `seed_fishers`,
  AUTO DEMO viewer sizes, `run_test_suite` report, live-settings draft read/save.
- `tests/test_bridge.py` (+231) — `importlib.util` + `HAS_TIKTOKLIVE`, skip decorator on
  `test_installed_tiktoklive_api`, plus the four Phase 10A tests.

Note: `save_live_settings` reads only `tiktokUsername` (no `username` fallback), so a
selected viewer can no longer leak into the draft.

## 5. Not done

- Real TikTok LIVE (`BRIDGE_MODE=live`) is still not connected; the live panel only saves a
  draft.
- `GET /host/telemetry` is real data, but fish / miss / NPC / fps / TOP-10 cards stay "Chưa
  có dữ liệu" until Roblox telemetry arrives — by design.
- Stress panel and camera/`/vitri` limits are unchanged from 9E/9G.
- No Publish, no TikTok, no `HttpService.HttpEnabled=true` left behind.
