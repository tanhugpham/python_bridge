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

1. This release keeps a **durable journal** (`BRIDGE_JOURNAL_PATH`, default
   `data/queue-journal.sqlite3`): pending rows, dedup ids, the ack history and the session
   id survive a process restart on the same instance, so Roblox keeps acking the same
   session and never processes a replayed event twice. Render's filesystem is still
   ephemeral, so a **redeploy or a spin-down starts an empty journal**; the producer
   replays whatever it has not seen acked (see 4) and Roblox drops repeats by `eventId`.
2. Render Free sleeps after prolonged idle. A new request may take about a minute to wake
   the service.
3. A local browser controller at `http://127.0.0.1:8787/host` **cannot submit to a
   different Render process by itself**. Hosting the bridge does not automatically move or
   connect your local HTML UI. The mirror (`BRIDGE_MIRROR_URL` on the local bridge) is the
   supported path, and it runs its own durable outbox (`BRIDGE_OUTBOX_PATH`).
4. Producers should keep their own outbox until `GET /v1/acks?after=<cursor>` reports the
   eventId acked. The mirror already does this; a future live ingest path must do the same.
   `BRIDGE_OUTBOX_TTL` (default 1800 s) bounds that outbox.
5. The Roblox Local Secret / Creator Secret must use the Render hostname with the correct
   read key. Do not copy secret values into scripts.
6. Keep Roblox HTTP and BridgeEnabled off unless authorized for a specific integration test.

