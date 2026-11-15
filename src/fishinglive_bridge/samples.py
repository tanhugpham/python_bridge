"""Mock batches used when no livestream is connected."""

from __future__ import annotations

from typing import Any


def join_batch(count: int = 15, start_timestamp: float = 1_700_000_000) -> list[dict[str, Any]]:
    events = []
    for index in range(1, count + 1):
        payload: dict[str, Any] = {"text": "cau"}
        if index == count:
            payload["score"] = 9999
        events.append({
            "eventId": f"join-{index:02d}",
            "type": "comment",
            "viewerId": f"v-{index:02d}",
            "nickname": "<b>Hà</b>" if index == 1 else f"Khách {index}",
            "timestamp": start_timestamp + index,
            "payload": payload,
        })
    return events


def interaction_batch(timestamp: float = 1_700_000_100) -> list[dict[str, Any]]:
    return [
        {
            "eventId": "follow-01",
            "type": "follow",
            "viewerId": "v-01",
            "nickname": "Hà",
            "timestamp": timestamp,
            "payload": {},
        },
        {
            "eventId": "like-01",
            "type": "like",
            "viewerId": "v-01",
            "nickname": "Hà",
            "timestamp": timestamp + 1,
            "payload": {},
        },
        {
            "eventId": "gift-01",
            "type": "gift",
            "viewerId": "v-01",
            "nickname": "Hà",
            "timestamp": timestamp + 2,
            "payload": {"amount": 1},
        },
    ]
