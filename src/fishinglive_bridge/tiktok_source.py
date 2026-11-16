"""TikTokLive 7.0.1 listener. Outbound chat is refused."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable

from fishinglive_bridge.config import Settings, normalize_username
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
        self.username = settings.tiktok_username
        self.session_id = settings.session_id
        self.target_idc = settings.target_idc
        self.connected = False
        self.last_error = ""
        self.switches = 0
        self.updated_at = time.time()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "username": self.username,
                "connected": self.connected,
                "lastError": self.last_error,
                "switches": self.switches,
                "hasSession": bool(self.session_id),
                "updatedAt": self.updated_at,
            }

    def apply(self, username: str, session_id: str = "", target_idc: str = "") -> dict[str, Any]:
        """Switch the live room without a redeploy. Thread-safe; no secret is returned."""
        name = normalize_username(username)
        if not name:
            raise ValueError("invalid_username")
        if session_id and not target_idc:
            raise ValueError("target_idc_required")
        with self._lock:
            if not session_id:
                # Username-only switch keeps the session the process already has.
                session_id = self.session_id
                target_idc = self.target_idc
            self.username = name
            self.session_id = session_id
            self.target_idc = target_idc
            self.switches += 1
            self.updated_at = time.time()
            self.last_error = ""
            self.connected = False
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._kick)
        return self.status()

    def _kick(self) -> None:
        """Run in the asyncio loop: drop the current client so run() reconnects."""
        client = self.client
        self.client = None
        if client is not None and getattr(client, "connected", False):
            asyncio.create_task(self._close_client(client))
        wake = self._wake
        if wake is not None:
            wake.set()

    async def _close_client(self, client: Any) -> None:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 - a dead socket must not stop the switch
            pass

    def _refuse_outbound(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("outbound TikTok chat is disabled")

    def _open(self) -> None:
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import CommentEvent, FollowEvent, GiftEvent, LikeEvent

        with self._lock:
            username = self.username
            session_id = self.session_id
            target_idc = self.target_idc
        client = TikTokLiveClient(unique_id=username)
        client.send_room_chat = self._refuse_outbound
        if session_id:
            client.web.set_session(session_id, target_idc)
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
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
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
                with self._lock:
                    self.connected = True
                    self.last_error = ""
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
                        with self._lock:
                            self.connected = False
                            self.last_error = type(error).__name__
                        self._dropped()
                        logger.warning("tiktok connection ended: %s", type(error).__name__)
                    elif not connection.cancelled():
                        with self._lock:
                            self.connected = False
            except Exception as exc:
                with self._lock:
                    self.connected = False
                    self.last_error = type(exc).__name__
                self._dropped()
                logger.warning("tiktok reconnect scheduled after %s", type(exc).__name__)
            finally:
                if waiter is not None and not waiter.done():
                    waiter.cancel()
            if stop.is_set():
                break
            await self._sleep_or_wake(stop, delay)
            delay = min(delay * 2, self.settings.max_backoff)
        with self._lock:
            self.connected = False
        client = self.client
        if client is not None and getattr(client, "connected", False):
            await client.disconnect()

    async def _sleep_or_wake(self, stop: asyncio.Event, delay: float) -> None:
        """Back off, but reconnect immediately after an operator switch."""
        wake = self._wake
        waiting = [asyncio.create_task(stop.wait())]
        if wake is not None:
            waiting.append(asyncio.create_task(wake.wait()))
        done, pending = await asyncio.wait(waiting, timeout=delay, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if not done:
            return
        if wake is not None and wake.is_set():
            wake.clear()
