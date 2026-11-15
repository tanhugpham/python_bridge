"""TikTokLive 7.0.1 listener. Outbound chat is refused."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from fishinglive_bridge.config import Settings
from fishinglive_bridge.normalize import (
    EventIdFactory,
    normalize_comment,
    normalize_follow,
    normalize_gift,
    normalize_like,
)

logger = logging.getLogger("fishinglive_bridge.tiktok")
Publish = Callable[[dict[str, Any]], tuple[str, int | None]]


class TikTokSource:
    def __init__(self, settings: Settings, publish: Publish, on_drop: Callable[[], Any] | None = None, avatars: Any = None) -> None:
        self.settings = settings
        self.publish = publish
        self.on_drop = on_drop
        self.avatars = avatars
        self.factory = EventIdFactory()
        self.client = None

    def _refuse_outbound(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("outbound TikTok chat is disabled")

    def _open(self) -> None:
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import CommentEvent, FollowEvent, GiftEvent, LikeEvent

        client = TikTokLiveClient(unique_id=self.settings.tiktok_username)
        client.send_room_chat = self._refuse_outbound
        if self.settings.session_id:
            client.web.set_session(self.settings.session_id, self.settings.target_idc)
        client.add_listener(CommentEvent, self._on_comment)
        client.add_listener(FollowEvent, self._on_follow)
        client.add_listener(LikeEvent, self._on_like)
        client.add_listener(GiftEvent, self._on_gift)
        self.client = client

    def _emit(self, event: dict[str, Any] | None) -> None:
        if event is None:
            return
        status, _sequence = self.publish(event)
        if self.avatars is not None and event["type"] == "comment":
            text = event["payload"].get("text", "")
            if isinstance(text, str) and text.strip().lower() in {"cau", "câu"}:
                self.avatars.note_join(event["viewerId"])
        if status not in {"created", "duplicate", "coalesced"}:
            logger.info("event skipped: %s", status)

    def _dropped(self) -> None:
        if self.on_drop is not None:
            self.on_drop()

    async def _on_comment(self, event: Any) -> None:
        self._emit(normalize_comment(event, self.factory))

    async def _on_follow(self, event: Any) -> None:
        self._emit(normalize_follow(event, self.factory))

    async def _on_like(self, event: Any) -> None:
        self._emit(normalize_like(event, self.factory))

    async def _on_gift(self, event: Any) -> None:
        self._emit(normalize_gift(event, self.factory))

    async def run(self, stop: asyncio.Event) -> None:
        delay = 1.0
        while not stop.is_set():
            waiter = None
            try:
                self._open()
                connection = await self.client.start(
                    fetch_live_check=True,
                    fetch_gift_info=False,
                    fetch_room_info=False,
                )
                delay = 1.0
                waiter = asyncio.create_task(stop.wait())
                done, _pending = await asyncio.wait(
                    {connection, waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop.is_set() or waiter in done:
                    break
                if connection.done():
                    error = connection.exception()
                    if error is not None:
                        self._dropped()
                        logger.warning("tiktok connection ended: %s", type(error).__name__)
            except Exception as exc:
                self._dropped()
                logger.warning("tiktok reconnect scheduled after %s", type(exc).__name__)
            finally:
                if waiter is not None and not waiter.done():
                    waiter.cancel()
            if stop.is_set():
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, self.settings.max_backoff)
        client = self.client
        if client is not None and getattr(client, "connected", False):
            await client.disconnect()
