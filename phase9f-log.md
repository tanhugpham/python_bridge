# PHASE 9F log — MOCK end-to-end + AUTO DEMO

Place 131643984728821 (Hung1) / universe 10776877200. MOCK path only: no HTML tab was opened, no TikTok LIVE, no Publish.
Publish source: `tiktok-bridge/phase9f-drive.ps1` posts the same event shapes the HTML `/host/action` posts, straight to `POST /v1/events` on Render (`https://python-bridge-6v9w.onrender.com`, session `7b387d5b87b74a2e91db454b722ec7d7`).

## Gate before Play

- `BridgeConfig.BridgeEnabled` set `true`, `BridgeMode=mock`, `HttpService.HttpEnabled=true` — owner-approved temporary window for this phase only.
- Local Secret `FISHINGLIVE_BRIDGE_READ_KEY` already domain-bound to `python-bridge-6v9w.onrender.com` (9E PASS), so the poller authenticated on the first poll.
- `FishingBaitConfig.approvedForLive` was `false` (place default). Backup: `backups/phase9f/roblox/FishingBaitConfig.lua.txt`.

## Results

| # | Check | Result | Evidence (server-side attributes / feed) |
|---|---|---|---|
| 1 | 500-like unlock | **PASS** | `LikeCount=500`, `LikeNeed=500`, `LikeUnlocked=true`, `LikeUser=MinhFishing9F`; feed seq 500 `🎣 @MinhFishing9F ĐÃ MỞ KHÓA QUYỀN CÂU CÁ!` |
| 2 | `/câu` register + seat | **PASS** | `FisherRegistry=1`, `FisherVisible=1`, `pullWaiting=1`; rig `Workspace.FishingLive_Fishers.FLRig` with `FLViewer=u9f_minh`, `FLNick=@MinhFishing9F`, `FLState=FISHING` |
| 3 | `/giật` pull → result | **PASS** | feed 501 `🐟 … ĐANG GIỮ MỘT CON CÁ!`, 502 `⚠️ CÁ ĐANG VÙNG VẪY!`, 503 `BÌNH LUẬN /GIẬT ĐỂ HỖ TRỢ GIỮ CÁ!`, 504 `… BẮT ĐƯỢC CÁ DIÊU HỒNG! 1.4 KG – COMMON – 17 ĐIỂM`; `ShowcaseActive` sampled 139×; `pullMax=1` |
| 4 | `/vitri` position focus | **PASS** | `posMax={on=true, phase="POSITION_FOCUS", name="MinhFishing9F", mark="M", dist=18, gen=6}`; `PositionOn` sampled true 30× (≈4.5 s); `PositionGen 5 → 7`. No source patch needed once the viewer owned a rig |
| 5 | Gift 20 xu | **PASS** | feed 505 `🎁 Cảm ơn @LanFan9F đã gửi quà!`; `GiftGen 5 → 6` (gift shot below the 30-xu camera threshold is thank-you only, as designed) |
| 6 | Gift 30 xu → camera | **PASS** | `GiftGen 6 → 7`; `GiftOn=true` 13 samples; `CameraPhase=GIFT_CENTER`; feed 506 thank-you |
| 7 | Bait with live gate OFF | **PASS (expected block)** | 5 bait gifts → feed 507–511 thank-you lines only; `BaitOn` never true; `BaitGen=0`; `BaitTier=0`. Confirms `FishingBaitManager.noteGift` is gated by `Config.approvedForLive` |
| 8 | Bait tiers 1–5 with gate ON (temporary) | **PASS** | `BaitGen 0 → 6`; tier 1 `MỒI GIUN ĐỎ – CẤP 1` `rgb(196,48,42)`, tier 2 `MỒI TÔM BẠC – CẤP 2` `rgb(198,206,214)`, tier 5 `MỒI RỒNG THẦN – CẤP 5` `rgb(154,78,214)`; individual run: gen 7 tier 3 `MỒI CÁ VÀNG – CẤP 3`, gen 8 tier 4 `MỒI NGỌC BIỂN – CẤP 4`, gen 9 tier 5; `BaitUser=LanFan9F`; `BaitNotice=🎣 @LanFan9F ĐÃ ĐỔI MỒI!` |
| 9 | Dedup (like + gift) | **PASS** | like: `created seq=1031` then `duplicate seq=1031`; gift: `created seq=1040` then `duplicate seq=1040`, no extra feed line |
| 10 | Bridge chain | **PASS** | `BridgeState=polled`, `BridgePolls=406`, `BridgeAcks=35`, `BridgeLastError=""`; queue depth returned to 0 after each poll |
| 11 | FPS | **PASS** | Studio focused: client avg 60.4 fps (min 54 / max 62) over 8 s, `PerformanceStats.CPU=14.8 ms`, `GPU=4.2 ms`, `FrameRateManager.RenderAverage=17.3 ms`, 149 batches, viewport 456×675 |
| 12 | RAM | **PASS (recorded)** | `PerformanceStats.Memory=1180 MB`, `MaxMemory=1300 MB`; server `GetTotalMemoryUsageMb()=919.6 MB`; 5342 workspace descendants, 10 rigs, 1 player, `workspace:GetRealPhysicsFPS()=60.0` |
| 13 | AUTO DEMO | **implemented, PASS at bridge level** | see below |

### 11-fps measurement note

The first client sample read 14–16 fps with `GPU=0.001 ms` and `FrameRateManager.AverageFPS=16.6` — that was window-occlusion throttling, not game cost: Studio was behind other windows (`SetForegroundWindow(handle 328808)` fixed it). Re-sampled after focus: 60 fps. Do not treat the 15-fps number as a regression.


## AUTO DEMO (was missing)

`Select-String -Pattern 'AutoDemo|AUTO DEMO'` across the repo matched only `FISHINGLIVE_NEXT_TASK.md` and `phase9e-log.md` — no code anywhere, so it was implemented now:

- `src/fishinglive_bridge/host_state.py`: `AUTO_DEMO_VIEWERS=10`, `AUTO_DEMO_PACE=0.75`, `AUTO_DEMO_REST=3.0`, `AUTO_DEMO_LIKE_BURST=100`; `start_auto_demo()`, `stop_auto_demo()`, `_auto_demo_plan()`, `_auto_demo_loop()`; `dashboard()["autoDemo"]`; `stop_session()` / `reset_mock()` also stop it.
- Actions `auto_demo_start` / `auto_demo_stop` in `HostState.act` (stop is accepted even when the mock session is paused, same as `stress_stop`), plus `GET /host/auto-demo` in `api.py`.
- `host-controller/index.html` + `app.js`: card "AUTO DEMO 10 người xem" with start/stop, a live step line, and an `AUTO DEMO` dashboard card.
- `tests/test_bridge.py::test_host_auto_demo_ten_viewers_and_stop`.

Wave script per viewer set (`mock_0001` … `mock_0010`): 5×100 likes + `/câu` for the lead, `/giật` support, `/vitri` from the seated fisher, 5×100 likes + `câu` for the helper, gift 20 xu, gift 30 xu, 5 bait tiers from 5 other viewers, one chat line; then it loops the next wave until stopped.

Live run against a local bridge (`127.0.0.1:8787`, the same endpoints the HTML page uses, CSRF cookie + `X-CSRF-Token` + `Origin`):

| Step | Result |
|---|---|
| `POST /host/action {action:auto_demo_start}` | `ok=true`, `bridgeStatus=ACCEPTED_BY_BRIDGE`, `appliedOnRoblox=false`, `viewers=["mock_0001"…"mock_0010"]` (10) |
| `GET /host/auto-demo` after 5 s | `running=true, sent=7, wave=1, step="hỗ trợ /giật cho mock_0001"`; dashboard `viewers=10, totalLikes=500, depth=7` |
| after 19 s | `sent=26, wave=2, step="tim 100 cho mock_0001 (4/5)"`; dashboard `totalLikes=1400, totalGifts=7`; log types `like=14, comment=5, gift=7` |
| `POST /host/action {action:auto_demo_stop}` | `ok=true, sent=31`; then `running=false, note=stopped, step="đã dừng"` |
| second start while running | `ok=false` (`busy`) — single worker enforced |
| stop → start again (restart safety) | run1 `sent=3` / `stop1.sent=3`; run2 fresh 10 viewers `sent=3` after 2 s, `sent=7` after 5 s (one worker at 0.75 s pace, not doubled); `stop2.sent=7`; 2 s later `running=false, sent=7` (the old thread does not write into the new run) |

The worker receives its own `state` dict and its own `threading.Event` (`_auto_demo_loop(names, state, stop)`), so a stop+restart cannot let the previous run keep publishing or overwrite the new run's counters.

`pytest tests -q`: 17 passed, 1 deselected (`test_installed_tiktoklive_api` needs the `TikTokLive` package, absent in `.venv313` — environment only, unrelated to this change).

### AUTO DEMO limitation (honest)

Render still runs the previous build, so the HTML buttons and the new actions are **not live on `python-bridge-6v9w.onrender.com`** until `tiktok-bridge` is redeployed. Roblox can only reach that host (the Local Secret is domain-bound), so AUTO DEMO→Roblox was not replayed end-to-end; every event type it emits was individually verified E2E in rows 1–9.

## Cleanup (verified in Edit mode after Stop Play)

| Setting | Value |
|---|---|
| `HttpService.HttpEnabled` | `false` |
| `BridgeConfig.BridgeEnabled` (source) | `false` |
| `BridgeConfig.BridgeMode` | `mock` |
| `FishingBaitConfig.approvedForLive` (source) | `false` |
| Play | stopped |
| Local bridge process on 127.0.0.1:8787 | stopped |
| Publish / TikTok LIVE | not done / not connected |

## Notes

- No Roblox gameplay source edits were needed: `/vitri` started working once the viewer was registered and owned a rig, and bait was blocked only by the live gate, which is intentional.
- Pre-existing ambient error `waypoints-canh 2 gap -9 68` still appears in the server log; untouched, out of scope.
- `phase9f-out/*.txt` holds the raw driver replies (they appeared empty only because the tee write landed after the earlier read). Play/HTTP readback is the authority for every number above.

## Appendix — raw driver replies (`tiktok-bridge/phase9f-out/`)

```
02-actions.txt
/cau bridge=created seq=1020 id=9fcau-1794804144-1
/giat bridge=created seq=1021 id=9fgiat-1794804144-2
/vitri bridge=created seq=1022 id=9fvitri-1794804144-3

06-baits-gateoff.txt / 08-baits-approved.txt
BAIT_TIER_1 (10 xu) bridge=created seq=1026/1032
BAIT_TIER_2 (30 xu) bridge=created seq=1027/1033
BAIT_TIER_3 (50 xu) bridge=created seq=1028/1034
BAIT_TIER_4 (100 xu) bridge=created seq=1029/1035
BAIT_TIER_5 (200 xu) bridge=created seq=1030/1036

09-baits-individual.txt
BAIT_TIER_3 (50 xu) bridge=created seq=1037
BAIT_TIER_4 (100 xu) bridge=created seq=1038
BAIT_TIER_5 (200 xu) bridge=created seq=1039

07-dedup.txt
dup first -> created seq=1031
dup second -> duplicate seq=1031

10-dupgift.txt
dupgift first -> created seq=1040
dupgift second -> duplicate seq=1040
```
