"""TikTokLive 7.0.1 listener. Username-only by default and outbound chat is refused."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from importlib.metadata import version as _distribution_version
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

try:
    # Reported by /v1/tiktok so the panel and the docs can show the pinned client.
    TIKTOK_LIVE_VERSION = _distribution_version("TikTokLive")
except Exception:  # noqa: BLE001 - a missing package is reported by the connection error
    TIKTOK_LIVE_VERSION = ""

# Exception names surfaced as status["lastError"]. Matching on the name keeps the
# bridge importable when TikTokLive reshuffles its module tree, and each state the
# connector can be in gets its own code instead of a generic "connect failed":
#   UserOfflineError                          the room is simply not broadcasting
#   UserNotFoundError                         the handle does not exist
#   AuthenticatedWebSocketConnectionError     a session id was rejected / not whitelisted
#   ConnectError, JSONDecodeError             TikTok answered HTML / the network is down
#   FailedParseRoomIdError, MissingRoomIdInResponse
#                                             the room id could not be resolved
#   WebSocketNotOpen                          start() returned but no socket came up
#   AlreadyConnectedError                     a switch raced the previous socket
ERROR_CODES = (
    "UserOfflineError",
    "UserNotFoundError",
    "AuthenticatedWebSocketConnectionError",
    "FailedParseRoomIdError",
    "MissingRoomIdInResponse",
    "InvalidFetchIsLiveRequest",
    "AlreadyConnectedError",
    "ConnectError",
    "JSONDecodeError",
    "WebSocketNotOpen",
)
# After this many "not live" answers from the HTTP check one real connection is
# attempted anyway, so a wrong offline answer can never pin the bridge off.
OFFLINE_SKIP_LIMIT = 5
# Seconds to wait for the socket to open before the attempt counts as failed.
WS_OPEN_TIMEOUT = 5.0


def error_code(error: BaseException) -> str:
    """Map an exception onto the stable code shown in /v1/tiktok."""
    for base in type(error).__mro__:
        if base.__name__ in ERROR_CODES:
            return base.__name__
    return type(error).__name__ or "Error"



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
        # The room id is learned once and then reused, which is what lets a reconnect
        # skip the HTML scrape that TikTok refuses when it rate-limits this host.
        self.room_id = int(settings.tiktok_room_id or 0)
        self.connected = False
        self.last_error = ""
        self.switches = 0
        self.attempts = 0
        self.connects = 0
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
                # The room being off air is not a configuration fault, and the panel
                # says so instead of showing a red error for a normal state.
                "offline": self.last_error == "UserOfflineError",
                "sessionMode": "authenticated" if self.session_id else "username-only",
                "hasSession": bool(self.session_id),
                # A parked credential still in the environment, while the running
                # process talks to TikTok without it.
                "envHasSession": bool(self.settings.session_id),
                "roomId": self.room_id,
                "attempts": self.attempts,
                "connects": self.connects,
                "switches": self.switches,
                "tiktokLive": TIKTOK_LIVE_VERSION,
                "updatedAt": self.updated_at,
            }

    def apply(self, username: str, session_id: str | None = None, target_idc: str | None = None) -> dict[str, Any]:
        """Switch the live room without a redeploy. Thread-safe; no secret is returned.

        ``session_id=None`` keeps whatever the process already uses. An explicit empty
        string drops the session for this process only (username-only mode) while the
        stored credential in the environment is left untouched, so a restart restores it.
        """
        name = normalize_username(username)
        if not name:
            raise ValueError("invalid_username")
        if session_id is not None:
            if not isinstance(session_id, str):
                raise ValueError("invalid_session")
            session_id = session_id.strip()
        if target_idc is not None:
            if not isinstance(target_idc, str):
                raise ValueError("invalid_session")
            target_idc = target_idc.strip()
        if session_id and not target_idc:
            raise ValueError("target_idc_required")
        with self._lock:
            if session_id is None:
                session_id = self.session_id
                target_idc = target_idc or self.target_idc
            elif session_id == "":
                # Clearing the session also clears its idc: an idc alone is unusable.
                target_idc = ""
            if name != self.username:
                self.room_id = int(self.settings.tiktok_room_id or 0)
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
        if client is not None:
            # Disconnect even mid-handshake: a socket that opens after an operator
            # switch would otherwise talk to the room nobody is watching any more.
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

    def _open(self) -> Any:
        """Build a client for the current username. No network call happens here.

        Username-only is the supported path: the sign server is asked for a signature
        for the anonymous room, and no session id is ever sent to it. Sending a
        broadcaster session through a shared sign server needs the
        WHITELIST_AUTHENTICATED_SESSION_ID_HOST override upstream, which is the path
        that gets accounts banned, so it is deliberately not set anywhere here.
        """
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import (
            CommentEvent,
            ConnectEvent,
            DisconnectEvent,
            FollowEvent,
            GiftEvent,
            LikeEvent,
        )

        with self._lock:
            username = self.username
            session_id = self.session_id
            target_idc = self.target_idc
        client = TikTokLiveClient(unique_id=username)
        client.send_room_chat = self._refuse_outbound
        if session_id:
            # Authenticated mode is still supported for a manual, whitelisted sign
            # server; it is never the default and the switch endpoint can drop it.
            client.web.set_session(session_id, target_idc)
        client.add_listener(ConnectEvent, self._on_connect)
        client.add_listener(DisconnectEvent, self._on_disconnect)
        client.add_listener(CommentEvent, self._on_comment)
        client.add_listener(FollowEvent, self._on_follow)
        client.add_listener(LikeEvent, self._on_like)
        client.add_listener(GiftEvent, self._on_gift)
        self.client = client
        return client

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

    async def _on_connect(self, event: Any) -> None:
        """The client's own ConnectEvent: the room socket is really up.

        Not published: the Roblox schema only carries comment/like/gift/follow, so
        this event exists to make /v1/tiktok truthful instead of guessing.
        """
        room = getattr(event, "room_id", 0) or 0
        try:
            room_id = int(room)
        except (TypeError, ValueError):
            room_id = 0
        with self._lock:
            self.connects += 1
            if room_id > 0:
                self.room_id = room_id
            self.connected = True
            self.last_error = ""
            self.updated_at = time.time()
        logger.info("tiktok room connected (room %s)", self.room_id or "unknown")

    async def _on_disconnect(self, _event: Any) -> None:
        with self._lock:
            self.connected = False
            self.updated_at = time.time()
        logger.info("tiktok room disconnected")

    async def _preflight(self, client: Any) -> bool | None:
        """HTTP-only live check: True live, False definitely off air, None unknown.

        This is the "check with is_live() before opening a socket" rule from the
        upstream docs: while the room is off air the bridge makes one cheap HTTP
        request per backoff window instead of repeatedly opening a websocket.
        """
        room_id = self.room_id
        try:
            if room_id:
                alive = bool(await client.web.fetch_is_live(room_id=int(room_id)))
            else:
                alive = bool(await client.is_live())
        except Exception as error:  # noqa: BLE001 - any failure here is only a hint
            code = error_code(error)
            if code == "UserNotFoundError":
                # A wrong handle is not going to fix itself by retrying a socket.
                with self._lock:
                    self.connected = False
                    self.last_error = code
                    self.updated_at = time.time()
                logger.warning("tiktok live check: %s", code)
                return False
            logger.info("tiktok live check inconclusive: %s", code)
            return None
        with self._lock:
            self.connected = False
            self.last_error = "" if alive else "UserOfflineError"
            self.updated_at = time.time()
        return alive

    async def _wait_open(self, client: Any, connection: asyncio.Task) -> bool:
        """start() returning is not proof of a socket; wait for it to actually open."""
        deadline = time.monotonic() + WS_OPEN_TIMEOUT
        while time.monotonic() < deadline:
            if getattr(client, "connected", False):
                return True
            if connection.done():
                return False
            await asyncio.sleep(0.2)
        return bool(getattr(client, "connected", False))

    def _remember_room(self, client: Any) -> None:
        try:
            room_id = int(getattr(client, "room_id", 0) or 0)
        except (TypeError, ValueError):
            return
        if room_id > 0:
            with self._lock:
                self.room_id = room_id

    async def _close(self, client: Any) -> None:
        await self._close_client(client)
        if self.client is client:
            self.client = None

    async def run(self, stop: asyncio.Event) -> None:
        delay = 1.0
        offline_skips = 0
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        while not stop.is_set():
            waiter = None
            client = None
            try:
                client = self._open()
                alive = await self._preflight(client)
                if alive is False and offline_skips < OFFLINE_SKIP_LIMIT:
                    # One HTTP answer per backoff window instead of a websocket that
                    # would only be rejected: this is what stops TikTok rate-limiting
                    # the host into answering HTML (the JSONDecodeError cycles).
                    offline_skips += 1
                    logger.info(
                        "tiktok room is off air (%s); next HTTP check in %ss without a websocket",
                        self.last_error or "UserOfflineError",
                        int(self.settings.max_backoff),
                    )
                    await self._close(client)
                    client = None
                    await self._sleep_or_wake(stop, self.settings.max_backoff)
                    continue
                offline_skips = 0
                with self._lock:
                    self.attempts += 1
                connection = await client.start(
                    fetch_live_check=True,
                    fetch_gift_info=False,
                    fetch_room_info=False,
                    # Once the room id is known it is reused, so a reconnect skips the
                    # HTML room scrape entirely (upstream's own reconnect recipe).
                    room_id=self.room_id or None,
                )
                opened = await self._wait_open(client, connection)
                if self.client is not client:
                    # An operator switch replaced this client mid-handshake: drop the
                    # stale socket and reconnect with the new settings, no error shown.
                    await self._close(client)
                    client = None
                    continue
                if not opened:
                    error = None
                    if connection.done() and not connection.cancelled():
                        error = connection.exception()
                    code = error_code(error) if error is not None else "WebSocketNotOpen"
                    with self._lock:
                        self.connected = False
                        self.last_error = code
                        self.updated_at = time.time()
                    self._dropped()
                    logger.warning("tiktok websocket did not open: %s", code)
                    await self._close(client)
                    client = None
                else:
                    self._remember_room(client)
                    delay = 1.0
                    with self._lock:
                        self.connected = True
                        self.last_error = ""
                        self.updated_at = time.time()
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
                                self.last_error = error_code(error)
                                self.updated_at = time.time()
                            self._dropped()
                            logger.warning("tiktok connection ended: %s", self.last_error)
                        elif not connection.cancelled():
                            with self._lock:
                                self.connected = False
            except Exception as exc:
                with self._lock:
                    self.connected = False
                    self.last_error = error_code(exc)
                    self.updated_at = time.time()
                self._dropped()
                logger.warning("tiktok reconnect scheduled after %s", self.last_error)
            finally:
                if waiter is not None and not waiter.done():
                    waiter.cancel()
            if stop.is_set():
                break
            await self._sleep_or_wake(stop, delay)
            delay = min(delay * 2, self.settings.max_backoff)
        with self._lock:
            self.connected = False
        live_client = self.client
        if live_client is not None:
            await self._close(live_client)

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
