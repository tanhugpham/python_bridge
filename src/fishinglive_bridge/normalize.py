"""Map TikTokLive 7.0.1 events into the FishingLive event schema.

Verified on the installed package:
CommentEvent.comment aliases content. User.unique_id aliases display_id.
FollowEvent is a SocialEvent. LikeEvent.count is the bundled like count.
GiftEvent.streaking is true only while a type-1 gift has repeat_end == 0.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from fishinglive_bridge.schema import clip_text, fit_event_id


class EventIdFactory:
    def __init__(self, window_seconds: float = 2.0) -> None:
        self.window_seconds = window_seconds

    def build(self, kind: str, source_id: str, viewer_id: str, timestamp: float, signature: str) -> str:
        if source_id:
            return fit_event_id(f"{kind}-{source_id}")
        bucket = int(timestamp // self.window_seconds)
        digest = hashlib.sha256(f"{kind}|{viewer_id}|{bucket}|{signature}".encode("utf-8")).hexdigest()[:16]
        return fit_event_id(f"d{kind}{digest}")


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    return getattr(obj, name, default)


def _source_id(event: Any) -> str:
    common = _attr(event, "common")
    msg_id = _attr(common, "msg_id", 0) or 0
    try:
        number = int(msg_id)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    return str(number)


def _timestamp(event: Any) -> float:
    common = _attr(event, "common")
    raw = _attr(common, "create_time", 0) or 0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0
    if value > 1_000_000_000_000:
        value = value / 1000
    if value <= 0 or value > 1_000_000_000_000:
        value = time.time()
    return value


def _viewer(event: Any) -> tuple[str, str] | None:
    user = _attr(event, "user")
    if user is None:
        return None
    unique = _attr(user, "unique_id") or _attr(user, "display_id") or ""
    if not isinstance(unique, str):
        unique = ""
    unique = unique.strip().lstrip("@")
    if unique == "":
        user_id = _attr(user, "id", 0) or 0
        try:
            number = int(user_id)
        except (TypeError, ValueError):
            number = 0
        if number <= 0:
            return None
        unique = str(number)
    nickname = _attr(user, "nickname") or ""
    if not isinstance(nickname, str):
        nickname = ""
    nickname = "".join(char for char in nickname if ord(char) >= 32)
    return unique[:64], clip_text(nickname, 80)


def _base(kind: str, letter: str, event: Any, payload: dict[str, Any], signature: str, factory: EventIdFactory) -> dict[str, Any] | None:
    viewer = _viewer(event)
    if viewer is None:
        return None
    viewer_id, nickname = viewer
    timestamp = _timestamp(event)
    return {
        "eventId": factory.build(letter, _source_id(event), viewer_id, timestamp, signature),
        "type": kind,
        "viewerId": viewer_id,
        "nickname": nickname,
        "timestamp": timestamp,
        "payload": payload,
    }


def normalize_comment(event: Any, factory: EventIdFactory) -> dict[str, Any] | None:
    text = _attr(event, "comment", None)
    if text is None:
        text = _attr(event, "content", "")
    if not isinstance(text, str):
        return None
    text = "".join(char for char in text if ord(char) >= 32).strip()
    if text == "":
        return None
    text = clip_text(text, 80)
    return _base("comment", "c", event, {"text": text}, text, factory)


def normalize_follow(event: Any, factory: EventIdFactory) -> dict[str, Any] | None:
    return _base("follow", "f", event, {}, "follow", factory)


def normalize_like(event: Any, factory: EventIdFactory) -> dict[str, Any] | None:
    count = _attr(event, "count", 1) or 1
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 1
    if count < 1:
        count = 1
    payload: dict[str, Any] = {}
    if count != 1:
        payload["count"] = count
    return _base("like", "l", event, payload, str(count), factory)


def normalize_gift(event: Any, factory: EventIdFactory) -> dict[str, Any] | None:
    # Intermediate streak updates are not rewards and are not enqueued.
    if bool(_attr(event, "streaking", False)):
        return None
    repeat = _attr(event, "repeat_count", 1) or 1
    try:
        repeat = int(repeat)
    except (TypeError, ValueError):
        repeat = 1
    if repeat < 1:
        repeat = 1
    if repeat > 20:
        repeat = 20
    group_id = _attr(event, "group_id", 0) or 0
    return _base("gift", "g", event, {"amount": repeat}, str(group_id), factory)
