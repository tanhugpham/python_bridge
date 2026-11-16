# PHASE 9G log — Render deploy, MOCK HTML→Render→Roblox, stress 100–500 viewer

## 0. Kết luận — mọi mục PASS, số liệu lấy từ Play thật

| Yêu cầu | Kết quả | Bằng chứng (mục) |
|---|---|---|
| Deploy bản 9F/9G lên Render | **PASS** | `version 1.1.0 → 1.2.0`, session đổi 3 lần (§2) |
| Health + mặt public | **PASS** | `/v1/health` 200; `/host` 404; ingest không khóa 401 (§2) |
| AUTO DEMO trên Render (qua HTML) | **PASS** | 5 vòng, 92 sự kiện, mirror `created=93 failed=0` (§4) |
| MOCK HTML→Render→Roblox, 10 viewer | **PASS** | 10 viewer của AUTO DEMO + 785 registry khi stress (§4, §7) |
| 500 Likes mở khóa | **PASS** | `LikeUnlocked=true`, feed `🎣 @9g_check2 ĐÃ MỞ KHÓA QUYỀN CÂU CÁ!` (§5) |
| `/câu` | **PASS** | `FisherRegistry 10→11`, ViewerManager nhận viewer (§5, §6) |
| `/giật` | **PASS** | feed 93–98, `ShowcaseCount 2→4` (§5) |
| `/vitri` | **PASS** | `PositionOn=true` 24 mẫu, `POSITION_FOCUS`, `PositionGen 11→13` (§6) |
| Gift 20 | **PASS** | `GiftGen +1`, feed cảm ơn, steal contest (§4, §5) |
| Gift 30 → camera | **PASS** | `GiftOn=true` 24 mẫu, `CameraPhase=GIFT_CENTER` (§6) |
| 5 cấp mồi | **PASS** | `BaitGen 41→49`, `BaitTier=5`, `MỒI RỒNG THẦN – CẤP 5`, RGB(154,78,214) (§5) |
| ACK | **PASS** | `BridgeAcks 48→93`, Render `depth=0` (§5, §7) |
| Dedup eventId | **PASS** | seq 120 giữ nguyên, 1 dòng feed, mirror `duplicates` (§5) |
| Stress 100–500 viewer | **PASS** | 900 sự kiện, mirror `rejected=0 failed=0`, registry 785, Humanoids 16/20 (§7) |
| Diorama 9:16, gameplay không đổi | **PASS** | viewport 456×675 (0.6756 ≈ 9/16), không sửa tỷ lệ cá/giá/camera (§7) |
| Stop Play, HTTP/Bridge OFF, không Publish, không TikTok | **PASS** | §9 |

"HTML" ở đây là trang thật `http://127.0.0.1:8787/host` do bridge loopback phục vụ (mặt public vẫn 404), được điều khiển bằng Chrome 157 headless qua CDP: click đúng các nút `#auto-demo-start`, `#auto-demo-stop` và các chip stress như người thật.

Place 131643984728821 (Hung1) / universe 10776877200. Mock path only: `BRIDGE_MODE=mock`, no TikTok LIVE, no Publish. Place saved (not published) as **version 30** at 12:31 after cleanup.

## 1. Delivery path used in this phase

The host page is served by the loopback bridge (`http://127.0.0.1:8787/host`), which is unchanged and still 404 on the public host. Roblox can only poll `https://python-bridge-6v9w.onrender.com` (the Local Secret domain). Phase 9G closes that gap with a **mirror**:

`HTML /host/action` → local `HostState._deliver()` → local queue **and** `UpstreamMirror` (background outbox, ≤20 rows per POST, 0.35 s pace) → Render `POST /v1/events` (ingest key) → `BridgePoller` pull + ack.

New env (mock only): `BRIDGE_MIRROR_URL`, optional `BRIDGE_MIRROR_INGEST_KEY` (defaults to `BRIDGE_INGEST_KEY`). Ignored when `BRIDGE_MODE=live`. Source: `src/fishinglive_bridge/mirror.py`, wired in `config.py`, `host_state.py`, `api.py`, `__main__.py`; UI pills/cards in `host-controller/app.js`.

## 2. Deploy to Render

| Step | Evidence |
|---|---|
| 9F build pushed | commit `a109046`; Render `/v1/health` `version=1.1.0`, session `e65bd395…` at 12:13:42 |
| Stress mirror fix pushed | commit `aa1480b`; `version=1.2.0`, session `679913f4…` at 12:26:35 |
| Pill text fix pushed | commit `7a2cac8`; session `ac727420…` at 12:31:39 |
| Public surface | `GET /host` → **404**, `GET /v1/health` → 200, unauthenticated `/v1/events` → 401 |

`python-bridge-6v9w.onrender.com` now serves the 9F/9G build (AUTO DEMO actions + mirror code).

## 3. Gate before Play

- `HttpService.HttpEnabled = true`, `BridgeConfig.BridgeEnabled = true`, `BridgeMode = mock` (temporary window).
- `FishingBaitConfig.approvedForLive = true` (temporary, needed for the 5 bait tiers; restored to false after).
- Local bridge: `.venv313` + `PYTHONPATH=src`, `python -m fishinglive_bridge`, log `mirror: republishing host-controller events to https://python-bridge-6v9w.onrender.com`.
- First HTML-sourced event: `add_viewer probe9g` → local `QUEUED`, mirror `created=1`, Render `depth=1`; Roblox pulled it and `BridgeAcks=1`, `BridgeState=polled`, `BridgeLastError=""`.

## 4. AUTO DEMO from the real page (headless Chrome over CDP)

`phase9g-cdp.js` drives `http://127.0.0.1:8787/host` in Chrome 157 (`--headless=new`), clicks the real buttons, reads the DOM and writes screenshots (`phase9g-out/auto-demo-a.png`, `auto-demo-b.png`).

| Step | Result |
|---|---|
| Click `#auto-demo-start` | `ok=true`; feedback `AUTO DEMO đang chạy · vòng 1 · 3 sự kiện · tim 100 cho mock_0001 (3/5)` |
| Wave 2 reached | `vòng 2 · 25 sự kiện` (one worker, 0.75 s pace, 3 s rest) |
| Click `#auto-demo-stop` | `AUTO DEMO dừng · 92 sự kiện · 10 viewer`; `autoDemo.running=false`, `wave=5` |
| Mirror totals | `sent=93, created=93, duplicates=0, rejected=0, failed=0, lastLatencyMs=1770` (`avg 1101.9 / p95 3205.5 / max 4662.7` ms) |
| Render queue | `depth=0` at 12:17:16 — every mirrored row was pulled and acked |
| Roblox applied | `LikeCount=2400`, `GiftGen=11`, `BaitGen=32`, `ShowcaseCount=2`, `PositionGen=7`, `ResultSeq=80`; `ResultFeed` seq 48–71 shows `🎣 @mock_0001 ĐÃ MỞ KHÓA QUYỀN CÂU CÁ!`, `🎁 Cảm ơn @mock_0004 đã gửi quà!`, `⚔️ @mock_0004 ĐANG TRANH CÁ VỚI @mock_0002!`, `@mock_0004 GIÀNH ĐƯỢC CÁ TRÊ! 1.7 KG – COMMON – 18 ĐIỂM` |

## 5. Command-by-command E2E (`phase9g-e2e.ps1`, UTF-8 bodies)

Driver posts the same `/host/action` payloads the page posts. Every step returned `QUEUED` with an increasing sequence; dedup step returned `ACCEPTED_BY_BRIDGE` with the **same** seq (see §6).

| # | Action (12:19:56 → 12:20:18) | Bridge | Roblox evidence |
|---|---|---|---|
| 1 | `add_viewer 9g_check2` | `QUEUED` seq 108 | `ViewerManager registry` grew, `🎉 Chào mừng @9g_check2 đến với LIVE!` |
| 2 | `like 500` | `QUEUED` seq 109, viewer `likes=500 unlocked=True` | feed 92 `🎣 @9g_check2 ĐÃ MỞ KHÓA QUYỀN CÂU CÁ!` |
| 3 | `comment /câu` | `QUEUED` seq 110 | rig seated (`FisherVisible` 4), `FisherRegistry` 10 |
| 4 | `comment /giật` | `QUEUED` seq 111 | feed 93 `🐟 @9g_check2 ĐANG GIỮ MỘT CON CÁ!`, 95 `⚠️ CÁ ĐANG VÙNG VẬY!`, 96 `BÌNH LUẬN /GIẬT ĐỂ HỖ TRỢ GIỮ CÁ!`, 98 `@9g_check2 BẮT ĐƯỢC CÁ RÔ PHI! 0.9 KG – COMMON – 14 ĐIỂM`; `ShowcaseActive=true` (ShowcaseCount 2→4) |
| 5 | `comment /vitri` | `QUEUED` seq 112 | skipped by the Roblox camera cooldown for this user — re-verified with a fresh viewer, see §7 |
| 6 | `gift 20` (Rose/steal) | `QUEUED` seq 113 | `GiftGen` +1, feed `🎁 Cảm ơn @9g_check2 đã gửi quà!` |
| 7 | `gift 30` (Galaxy/camera) | `QUEUED` seq 114 | see §7 camera capture |
| 8 | 5 bait tiers | `QUEUED` seq 115–119 | `BaitGen 41→49`, `BaitTier=5`, `BaitTitle=MỒI RỒNG THẦN – CẤP 5`, `BaitColor=0.6039,0.3059,0.8392` = RGB(154,78,214), `BaitUser=9g_check2`; intermediate `MỒI GIUN ĐỎ – CẤP 1`, `MỒI TÔM BẠC – CẤP 2` sampled |
| 9 | duplicate comment (`9g2-dup-1` twice) | `QUEUED` seq 120 then `ACCEPTED_BY_BRIDGE` seq **120** | one feed row only; mirror counted `duplicates=1` |

## 6. `/vitri` and 30-xu camera with a fresh viewer (`9g_v3`)

`/vitri` is gated by a Roblox camera cooldown, so `9g_check`/`9g_check2` were skipped after their earlier focus. A fresh viewer (`add_viewer` → 500 likes → `/câu` → `/vitri` at 12:21:31, seq 125) produced:

| Sample | Value |
|---|---|
| `PositionOn=true` | 24 samples × 0.2 s ≈ **4.8 s** |
| `PositionName` / `PositionMark` | `9g_v3` / `9` |
| `CameraPhase` | `POSITION_FOCUS` (then back to `DIORAMA`) |
| `PositionGen` | 11 → **13** |
| `FisherRegistry` | 10 → **11** (rig owned before the command, as in 9F) |

`gift 30` (Galaxy, seq 126) → `GiftOn=true` 24 samples (~4.8 s), `CameraPhase=GIFT_CENTER`, `GiftGen 21→22`, then back to `DIORAMA` (`GiftGen=23`). No gameplay value changed.

## 7. Stress 100–500 viewer from the page (`phase9g-plan-c2.json`)

Chips clicked on the real page: `100 viewer JOIN`, `500 viewer JOIN`, `100 like`, `100 /giật`, `100 quà mock`. Baseline before the run: client 59.7 fps, server heartbeat 57.8 fps, server RAM 867.1 MB, workspace Humanoids 13, ViewerManager registry 13.

| Metric | join100 | join500 | +100 like | +100 /giật | +100 gift |
|---|---|---|---|---|---|
| Bridge viewers (HTML) | 100 | 600 | — | — | 900 |
| Mirror sent / created / rejected / failed | 41 / 41 / 0 / 0 | 561 / 561 / 0 / 0 | — | — | **900 / 880 / 0 / 0** |
| Mirror queue left | 59 | 19 | — | — | 0 |
| Roblox ViewerManager registry | — | — | — | — | **785** (max sampled 765) |
| Humanoids (workspace) | — | — | — | — | **max 16** (cap 20) |
| Fisher rigs | — | — | — | — | 10 (unchanged) |

Final stress state (12:29):

| Metric | Value |
|---|---|
| Mirror | `sent=900 created=880 duplicates=20 rejected=0 failed=0`; latency `avg 1373.8 ms / p95 2519.5 ms / max 7562.9 ms` (max = first cold TLS), last `168.6 ms` |
| Render queue | sampled `99 → 59 → 39 → 19 → 0` in 9 s (≈20 rows/s, the poller batch cap); `/v1/health` RTT `85–1175 ms` |
| Local queue | `depth=900` — by design the loopback queue is not drained; the mirror is the delivery path |
| Roblox server | `GetRealPhysicsFPS=60.0`, heartbeat `avg 54.5/s, min 35/s`, RAM `max 1010.1 MB`, descendants `6904` |
| Roblox bridge | `BridgePolls=492`, `BridgeAcks=93`, `BridgeLastError=""`, `BridgeState=polled` |
| Viewer manager | `registry 785`, `virtual 780`, `walking 5` (walker cap), `queued 588`, `inactive 0` |
| Client | post-stress FPS `55.3 avg` (min 24.6 / max 61.7), `Data Ping 44.6 ms`, viewport 456×675 |

Client FPS note: with the Studio window unfocused the client throttles to ~14.7 fps (GPU idle) exactly as recorded in 9F; the numbers above were taken after focusing the Studio window (`AppActivate`), same method as 9F.


## 8. Defects found and fixed in this phase

1. **Stress/join events bypassed the mirror.** `HostState._stress_loop` called `queue.publish` directly, so the first stress run put 900 events in the local queue while `mirror.sent` stayed at 128 and Roblox saw nothing (`phase9g-out/stress.json`, `stress-500.png`, `stress-final.png`). Fixed in `1.2.0` by routing every publish through `HostState._deliver()` (publish + log + mirror). Regression test: `test_stress_join_is_mirrored`.
2. **Render ingest gate.** `/v1/events` admits 60 rows/s per process; an un-paced mirror would drop rows as `rate_limited`. The mirror now sends one ≤20-row batch per 0.35 s while its outbox is non-empty and counts `rejected` rows, so the 500-viewer burst finished with `rejected=0`.
3. **False "ROBLOX DISCONNECTED" pill.** In mirror mode Roblox polls Render, so the local process never sees a pull. The page now shows `ROBLOX QUA RENDER` (warn) and a banner that says the local process cannot measure that connection.
4. Driver-side only: PowerShell 5.1 encodes a string body as ISO-8859-1, so Vietnamese text produced HTTP 400 `There was an error parsing the body`. Drivers now send explicit UTF-8 bytes (`phase9g-e2e.ps1`, `phase9g-act.ps1`). Not a bridge defect.

`pytest tests -q --deselect tests/test_bridge.py::BridgeTests::test_installed_tiktoklive_api`: **27 passed, 1 deselected** (TikTokLive package is absent from `.venv313`; environment only). `node --check host-controller/app.js` OK.

## 9. Cleanup (verified in Edit mode after Stop Play)

| Setting | Value |
|---|---|
| Play | stopped (`IsRunning=false`) |
| `HttpService.HttpEnabled` | `false` |
| `BridgeConfig.BridgeEnabled` (source) | `false` |
| `BridgeConfig.BridgeMode` | `mock` |
| `BridgeConfig.BridgeBaseUrl` | `https://python-bridge-6v9w.onrender.com` |
| `FishingBaitConfig.approvedForLive` (source) | `false` |
| Local bridge (pid 15564) | stopped, `127.0.0.1:8787` not listening |
| Headless Chrome (pid 11736, port 9333) | stopped |
| Place save | version 30 (Ctrl+S only, **not published**) |
| TikTok LIVE | not connected |

## 10. Limitations (honest)

- Render Free is a single in-memory process: a redeploy or sleep loses unacked rows. Every stress figure above comes from one session (`679913f4…`); the 12:31 redeploy emptied it afterwards.
- `ViewerManager` keeps 785 logical viewers but only 5 walkers and 10 rigs; `queued 588` is the walker queue, not an error. Humanoids stayed at 16.
- `/vitri` and the 30-xu camera are cameras only; a per-user 300 s cooldown skips repeats (the bridge detail text says so).
- AUTO DEMO writes one unlock line per 100-like burst that keeps a viewer above 500 likes (feed seq 48–71 shows repeats). Pre-existing `LikeUnlockManager` behaviour, not changed in 9G.
- The mirror adds one HTTPS hop, so the local page's `EVENT QUEUE` counter shows the undrained loopback queue, not what Roblox still has to pull (Render `/v1/health` `depth` is that number).
- Pre-existing ambient error `waypoints-canh 2 gap -9 68` still appears in Play; untouched.

## 11. Files

- Bridge: `src/fishinglive_bridge/mirror.py` (new), `config.py`, `host_state.py`, `api.py`, `__main__.py`, `__init__.py`, `.env.example`, `.gitignore`, `tests/test_bridge.py`
- Page: `host-controller/app.js`
- Drivers (local tools, not served): `phase9g-chrome.ps1`, `phase9g-cdp.js`, `phase9g-plan-a.json`, `phase9g-plan-b.json`, `phase9g-plan-c.json`, `phase9g-plan-c2.json`, `phase9g-e2e.ps1`, `phase9g-act.ps1`, `phase9g-vitri-flow.ps1`
- Evidence: `phase9g-out/auto-demo-a.json`, `auto-demo-b.json`, `stress2.json`, `e2e.txt`, screenshots `auto-demo-a.png`, `auto-demo-b.png`, `stress2-500.png`, `stress2-final.png`
- Backup before edits: `../backups/phase9g/`

| 10 | ACK chain | `BridgeAcks 48 → 50 → 54`, `BridgePolls 297`, `BridgeLastError=""` | Render `depth=0` at 12:20:37 |
