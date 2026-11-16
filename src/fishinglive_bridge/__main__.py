"""Run the bridge. Mock mode does not open a TikTok connection."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from fishinglive_bridge.api import create_app
from fishinglive_bridge.avatar_cache import AvatarCache
from fishinglive_bridge.config import load_settings
from fishinglive_bridge.mirror import UpstreamMirror
from fishinglive_bridge.queue_store import EventQueue
from fishinglive_bridge.redact import configure_logging

logger = logging.getLogger("fishinglive_bridge")


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    settings = load_settings(root / ".env")
    configure_logging(settings.secret_values())
    queue = EventQueue(journal_path=settings.journal_path or None)
    if queue.durable:
        logger.info("queue journal: %s (%s recovered rows)", queue.status()["journal"], queue.recovered)
    avatars = AvatarCache()
    mirror = None
    if settings.mirror_url:
        # The host page is loopback-only; Roblox polls this public bridge instead.
        # The outbox is what makes the copy survive a restart or a Render redeploy.
        mirror = UpstreamMirror(
            settings.mirror_url,
            settings.mirror_key,
            outbox_path=settings.outbox_path or None,
            ttl=settings.outbox_ttl,
        )
        logger.info(
            "mirror: republishing host-controller events to %s (outbox %s)",
            settings.mirror_url,
            "durable" if mirror.durable else "memory",
        )

    live_source = None
    if settings.live:
        from fishinglive_bridge.tiktok_source import TikTokSource

        live_source = TikTokSource(
            settings,
            queue.publish,
            on_drop=queue.note_transport_drop,
            avatars=avatars,
        )

    @asynccontextmanager
    async def lifespan(_app):
        stop = asyncio.Event()
        task = None
        if live_source is not None:
            task = asyncio.create_task(live_source.run(stop))
            logger.info("live mode started for the configured username")
        else:
            logger.info("mock mode: TikTok client is not started")
        try:
            yield
        finally:
            stop.set()
            if mirror is not None:
                mirror.close()
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    control_dir = root.parent / "fishinglive-simulator"
    host_dir = root / "host-controller"
    if settings.loopback and not settings.live and (control_dir / "index.html").is_file():
        logger.info("control center on http://%s:%s/control", settings.host, settings.port)
    if settings.loopback and (host_dir / "index.html").is_file():
        logger.info("host controller on http://%s:%s/host", settings.host, settings.port)
    app = create_app(
        settings,
        queue,
        lifespan,
        avatars,
        control_dir=control_dir,
        host_dir=host_dir,
        mirror=mirror,
        live=live_source,
    )
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        ssl_certfile=settings.ssl_cert or None,
        ssl_keyfile=settings.ssl_key or None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
