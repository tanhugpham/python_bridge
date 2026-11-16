# FishingLive Bridge — Render Free (mock only)

This package intentionally excludes `.env`, credentials, runtime data, local virtual environments, and Git history.

## Create Render Web Service

- Repository root: `tiktok-bridge` (or set Render's Root Directory to `tiktok-bridge` if the repository contains a parent folder).
- Runtime: Python; Instance: Free.
- Build command: `pip install -r requirements.txt`
- Start command: `PYTHONPATH=src python -m fishinglive_bridge`
- Health check path: `/v1/health`

Add environment variables privately in Render Dashboard > Service > Environment:

| Name | Value |
| --- | --- |
| `BRIDGE_MODE` | `mock` |
| `BRIDGE_INGEST_KEY` | A private random value, at least 16 characters |
| `BRIDGE_READ_KEY` | A **different** private random value, at least 16 characters |

**Do not** set `BRIDGE_HOST`, `BRIDGE_PORT`, `BRIDGE_SSL_CERT`, or `BRIDGE_SSL_KEY` on Render. The code detects Render via its platform-provided `RENDER=true`, binds to `0.0.0.0`, and reads the platform's `PORT` (default 10000). Render provides public HTTPS; the app's internal listener uses HTTP.

Keep TikTok credentials empty. Do not switch `BRIDGE_MODE` to `live` without explicit approval.

Once deployed, open `https://<service-name>.onrender.com/v1/health`; the response should be 200. Test that unauthenticated `GET /v1/events` receives 401 and `/host` and `/control` return 404. Do not expose ingest credentials in HTML or browser JavaScript.

## Important limitations

1. This release retains an **in-memory event queue**. Render Free instances can restart or sleep, losing unacknowledged events. **Not production-ready for live gifts**, until a durable queue is implemented and tested.
2. Render Free sleeps after prolonged idle. A new request may take about a minute to wake the service.
3. A local browser controller at `http://127.0.0.1:8787/host` **cannot submit to a different Render process by itself**. Hosting the bridge does not automatically move or connect your local HTML UI. Keep HTML→Render→Roblox integration as a separate test phase.
4. The Roblox Local Secret / Creator Secret must use the Render hostname with the correct read key. Do not copy secret values into scripts.
5. Keep Roblox HTTP and BridgeEnabled off unless authorized for a specific integration test.
