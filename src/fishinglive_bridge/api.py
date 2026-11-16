"""Authenticated queue API. Roblox pulls and acks. It does not ingest."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from fishinglive_bridge import __version__
from fishinglive_bridge.avatar_cache import AvatarCache
from fishinglive_bridge.config import Settings, normalize_username
from fishinglive_bridge.host_state import HostState, host_allowed, origin_allowed
from fishinglive_bridge.queue_store import EventQueue

logger = logging.getLogger("fishinglive_bridge.api")
LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "localhost"}
TUNNEL_HEADERS = ("cf-ray", "cf-connecting-ip", "cf-visitor", "x-forwarded-for", "x-forwarded-host", "x-real-ip")


def _keys_match(given: str, expected: str) -> bool:
    import secrets

    if not given or not expected:
        return False
    try:
        return secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))
    except Exception:
        return False


def create_app(
    settings: Settings,
    queue: EventQueue,
    lifespan=None,
    avatars: AvatarCache | None = None,
    control_dir: Path | None = None,
    host_dir: Path | None = None,
    mirror=None,
    live=None,
) -> FastAPI:
    app = FastAPI(title="FishingLive Bridge", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    host = HostState(queue, settings, mirror)
    app.state.host = host
    app.state.live = live

    def require_ingest(x_bridge_key: str = Header(default="")) -> None:
        # Ingest stays on X-Bridge-Key. x-api-key is the Roblox read header only.
        if not _keys_match(x_bridge_key, settings.ingest_key):
            raise HTTPException(status_code=401, detail="unauthorized")

    def require_read(
        x_api_key: str = Header(default=""),
        x_bridge_key: str = Header(default=""),
    ) -> None:
        # Roblox HttpService sends the read secret as x-api-key.
        # An operator may still pass the same read key in X-Bridge-Key.
        # Every presented value must be the read key. The ingest key never qualifies.
        presented = [value for value in (x_api_key, x_bridge_key) if value]
        if not presented or any(not _keys_match(value, settings.read_key) for value in presented):
            raise HTTPException(status_code=401, detail="unauthorized")

    def require_bridge_key(
        x_bridge_key: str = Header(default=""),
        x_api_key: str = Header(default=""),
    ) -> None:
        # Read-only telemetry. The host page reaches the public bridge through the
        # mirror, which already holds that bridge's ingest key, so either secret may
        # ask. Events keep the stricter require_read / require_ingest below.
        presented = [value for value in (x_bridge_key, x_api_key) if value]
        allowed = (settings.read_key, settings.ingest_key)
        for key in allowed:
            if presented and all(_keys_match(value, key) for value in presented):
                return
        raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        state = queue.status()
        return {
            "ok": True,
            "version": __version__,
            "sessionId": state["sessionId"],
            "resetEpoch": state["resetEpoch"],
            "depth": state["depth"],
            "durable": state["durable"],
            "ackCursor": state["ackCursor"],
        }

    @app.get("/v1/tiktok", dependencies=[Depends(require_ingest)])
    def tiktok_status() -> dict[str, Any]:
        """Authenticated view of the live listener. Never returns a session value."""
        if not settings.live:
            return {"ok": True, "mode": settings.mode, "live": False, "note": "mock mode: TikTok client is not started"}
        if live is None:
            return {"ok": False, "mode": settings.mode, "live": True, "note": "live source is not running"}
        return {"ok": True, "mode": settings.mode, "live": True, **live.status()}

    @app.post("/v1/tiktok/switch", dependencies=[Depends(require_ingest)])
    def tiktok_switch(body: dict[str, Any]) -> dict[str, Any]:
        """Switch the live room at runtime (no redeploy). Session stays in memory only."""
        if not settings.live:
            raise HTTPException(status_code=409, detail="live_mode_required")
        if live is None:
            raise HTTPException(status_code=503, detail="live_source_unavailable")
        username = normalize_username(body.get("username", body.get("tiktokUsername")))
        if not username:
            raise HTTPException(status_code=422, detail="invalid_username")
        session_id = body.get("sessionId", body.get("session_id", ""))
        target_idc = body.get("targetIdc", body.get("target_idc", ""))
        if not isinstance(session_id, str) or not isinstance(target_idc, str):
            raise HTTPException(status_code=422, detail="invalid_session")
        session_id = session_id.strip()
        target_idc = target_idc.strip()
        if session_id and not target_idc:
            raise HTTPException(status_code=422, detail="target_idc_required")
        if len(session_id) > 4096 or len(target_idc) > 32:
            raise HTTPException(status_code=422, detail="invalid_session")
        try:
            state = live.apply(username, session_id, target_idc)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"ok": True, **state}

    @app.post("/v1/events", dependencies=[Depends(require_ingest)])
    def ingest(body: dict[str, Any]) -> dict[str, Any]:
        rows = body.get("events") if isinstance(body.get("events"), list) else [body]
        if len(rows) > 20:
            raise HTTPException(status_code=413, detail="batch_limit")
        results = []
        for row in rows:
            status, sequence = queue.publish(row)
            if isinstance(row, dict):
                host.observe_publish(row, status, sequence)
            results.append({"status": status, "sequence": sequence})
        state = queue.status()
        return {"results": results, "sessionId": state["sessionId"], "resetEpoch": state["resetEpoch"]}

    @app.get("/v1/acks", dependencies=[Depends(require_ingest)])
    def acks(after: int = 0, limit: int = 200) -> dict[str, Any]:
        """Producer-side ack tracking: which eventIds Roblox has confirmed.

        The mirror keeps its outbox until its rows appear here, so a restart or a
        spin-down of this service cannot silently drop unacked events.
        """
        if after < 0:
            raise HTTPException(status_code=400, detail="invalid")
        limit = max(1, min(500, limit))
        journal = getattr(queue, "journal", None)
        if journal is None:
            state = queue.status()
            return {
                "after": after,
                "next": after,
                "cursor": 0,
                "ids": [],
                "sessionId": state["sessionId"],
                "resetEpoch": state["resetEpoch"],
                "depth": state["depth"],
                "durable": False,
            }
        answer = journal.acks_after(after, limit)
        answer["durable"] = True
        return answer

    @app.get("/v1/events", dependencies=[Depends(require_read)])
    def pull(limit: int = 20) -> dict[str, Any]:
        status, rows = queue.pull(min(settings.batch_limit, limit))
        if status == "rate_limited":
            raise HTTPException(status_code=429, detail="rate_limited")
        host.note_pull(rows)
        state = queue.status()
        return {"events": rows, "sessionId": state["sessionId"], "resetEpoch": state["resetEpoch"]}

    @app.post("/v1/events/ack", dependencies=[Depends(require_read)])
    def ack(body: dict[str, Any]) -> dict[str, Any]:
        raw = body.get("sequences")
        if not isinstance(raw, list) or len(raw) > 20:
            raise HTTPException(status_code=400, detail="invalid")
        sequences: list[int] = []
        for item in raw:
            if isinstance(item, bool) or not isinstance(item, int):
                raise HTTPException(status_code=400, detail="invalid")
            sequences.append(item)
        status, removed = queue.ack(sequences)
        if status == "rate_limited":
            raise HTTPException(status_code=429, detail="rate_limited")
        # Roblox rides its FPS/RAM snapshot on this existing ACK call. Unknown or
        # out-of-range fields are dropped, never echoed back.
        telemetry = body.get("telemetry")
        if isinstance(telemetry, dict):
            host.telemetry.update(telemetry)
        if removed:
            host.note_ack(sequences)
        return {"acked": removed}

    @app.get("/v1/telemetry", dependencies=[Depends(require_bridge_key)])
    def telemetry() -> dict[str, Any]:
        state = queue.status()
        return {
            **host.telemetry.get(),
            "version": __version__,
            "sessionId": state["sessionId"],
            "resetEpoch": state["resetEpoch"],
            "depth": state["depth"],
            "ackCursor": state["ackCursor"],
            "lastAckAt": state["lastAckAt"],
        }

    @app.post("/v1/session/reset", dependencies=[Depends(require_ingest)])
    def reset_session(body: dict[str, Any]) -> dict[str, Any]:
        if body.get("confirm") is not True:
            raise HTTPException(status_code=400, detail="invalid")
        if avatars is not None:
            avatars.clear()
        session_id = queue.reset()
        return {"sessionId": session_id, "resetEpoch": queue.status()["resetEpoch"]}

    @app.get("/v1/overlay/viewers", dependencies=[Depends(require_read)])
    def overlay() -> dict[str, Any]:
        # Prepared for an OBS browser source. Images exist only in this process.
        images = avatars.viewers() if avatars is not None else []
        return {"sessionId": queue.status()["sessionId"], "viewers": images}

    def _tunnel_request(request: Request) -> bool:
        if any(request.headers.get(name) for name in TUNNEL_HEADERS):
            return True
        host_header = request.headers.get("host", "")
        lowered = host_header.lower()
        return "trycloudflare.com" in lowered or "cloudflare" in lowered

    def require_local_page(request: Request) -> None:
        client = request.client.host if request.client else ""
        if not settings.loopback or client not in LOOPBACK_CLIENTS or _tunnel_request(request):
            raise HTTPException(status_code=404, detail="not_found")
        origin = request.headers.get("origin")
        if origin and not origin_allowed(origin, settings.port):
            raise HTTPException(status_code=403, detail="origin")

    def require_host_page(request: Request) -> None:
        require_local_page(request)
        if not host_allowed(request.headers.get("host", "")):
            raise HTTPException(status_code=404, detail="not_found")
        fetch_site = request.headers.get("sec-fetch-site", "")
        if request.method == "POST" and fetch_site not in {"", "same-origin", "none"}:
            raise HTTPException(status_code=403, detail="origin")

    def require_host_csrf(request: Request) -> None:
        require_host_page(request)
        header = request.headers.get("x-csrf-token", "")
        cookie = request.cookies.get("fl_host_csrf", "")
        if not header or header != cookie or not host.token_ok(header):
            raise HTTPException(status_code=403, detail="csrf")

    @app.get("/control")
    def control_page(request: Request) -> FileResponse:
        require_local_page(request)
        page = control_dir / "index.html" if control_dir is not None else None
        if page is None or not page.is_file():
            raise HTTPException(status_code=404, detail="not_found")
        return FileResponse(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/control/status")
    def control_status(request: Request) -> dict[str, Any]:
        require_local_page(request)
        state = queue.status()
        return {"ok": True, "sessionId": state["sessionId"], "depth": state["depth"], "mode": settings.mode}

    @app.post("/control/events")
    def control_event(body: dict[str, Any], request: Request) -> dict[str, Any]:
        # The browser never sees the ingest key. This route exists only on a loopback bind.
        require_local_page(request)
        if settings.live:
            raise HTTPException(status_code=409, detail="live_blocked")
        status, sequence = queue.publish(body)
        if isinstance(body, dict):
            host.observe_publish(body, status, sequence)
            host.mirror_external(body, status)
        return {"status": status, "sequence": sequence, "depth": queue.status()["depth"]}

    def _host_file(name: str, media: str) -> FileResponse:
        page = host_dir / name if host_dir is not None else None
        if page is None or not page.is_file():
            raise HTTPException(status_code=404, detail="not_found")
        return FileResponse(page, media_type=media, headers={"Cache-Control": "no-store"})

    @app.get("/host")
    def host_page(request: Request) -> FileResponse:
        require_host_page(request)
        return _host_file("index.html", "text/html; charset=utf-8")

    @app.get("/host/styles.css")
    def host_css(request: Request) -> FileResponse:
        require_host_page(request)
        return _host_file("styles.css", "text/css; charset=utf-8")

    @app.get("/host/app.js")
    def host_js(request: Request) -> FileResponse:
        require_host_page(request)
        return _host_file("app.js", "text/javascript; charset=utf-8")

    @app.get("/host/session")
    def host_session(request: Request) -> dict[str, Any]:
        require_host_page(request)
        token = host.issue_token()
        state = queue.status()
        payload = {
            "ok": True,
            "csrf": token,
            "mode": settings.mode,
            "mockMode": not settings.live,
            "sessionId": state["sessionId"],
            "robloxLabel": host.roblox_label(state),
        }
        response = JSONResponse(payload)
        response.set_cookie(
            "fl_host_csrf",
            token,
            httponly=True,
            samesite="strict",
            path="/host",
            secure=False,
            max_age=8 * 3600,
        )
        return response

    @app.get("/host/dashboard")
    def host_dashboard(request: Request) -> dict[str, Any]:
        require_host_page(request)
        return host.dashboard()

    @app.get("/host/viewers")
    def host_viewers(request: Request, q: str = "", offset: int = 0) -> dict[str, Any]:
        require_host_page(request)
        return host.viewer_page(q, offset)

    @app.get("/host/baits")
    def host_baits(request: Request) -> dict[str, Any]:
        require_host_page(request)
        return host.baits()

    @app.get("/host/leaderboard")
    def host_leaderboard(request: Request) -> dict[str, Any]:
        require_host_page(request)
        return host.leaderboard()

    @app.get("/host/struggle")
    def host_struggle(request: Request) -> dict[str, Any]:
        require_host_page(request)
        return host.struggle()

    @app.get("/host/results")
    def host_results(request: Request) -> dict[str, Any]:
        require_host_page(request)
        return host.results()

    @app.get("/host/events")
    def host_events(request: Request, limit: int = 80) -> dict[str, Any]:
        require_host_page(request)
        return host.events(limit)

    @app.get("/host/metrics")
    def host_metrics(request: Request) -> dict[str, Any]:
        require_host_page(request)
        return host.metrics()

    @app.get("/host/stress")
    def host_stress(request: Request) -> dict[str, Any]:
        require_host_page(request)
        return host.dashboard()["stress"]

    @app.get("/host/auto-demo")
    def host_auto_demo(request: Request) -> dict[str, Any]:
        require_host_page(request)
        return host.dashboard()["autoDemo"]

    @app.get("/host/report")
    def host_report(request: Request) -> dict[str, Any]:
        require_host_page(request)
        return {"ok": True, "report": host.report}

    @app.get("/host/live-settings")
    def host_live_settings(request: Request) -> dict[str, Any]:
        require_host_page(request)
        return host.live_settings()

    @app.get("/host/telemetry")
    def host_telemetry(request: Request) -> dict[str, Any]:
        require_host_page(request)
        board = host.dashboard()
        return {
            "ok": True,
            "source": board["roblox"]["telemetrySource"],
            "ageSeconds": board["roblox"]["telemetryAgeSeconds"],
            "fresh": board["roblox"]["telemetryFresh"],
            "note": board["roblox"]["telemetryNote"],
            "telemetry": board["roblox"]["telemetry"],
            "render": board["render"],
        }

    @app.post("/host/action")
    def host_action(body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_host_csrf(request)
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="invalid")
        result = host.act(body)
        if result.get("statusCode") == 409:
            raise HTTPException(status_code=409, detail="live_blocked")
        return result

    return app
