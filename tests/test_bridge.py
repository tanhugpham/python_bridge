"""Queue, schema, streak, auth, and restart tests. No TikTok connection."""

from __future__ import annotations

import importlib.metadata as metadata
import importlib.util
import json
import os
import struct
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from fishinglive_bridge.api import create_app
from fishinglive_bridge.avatar_cache import AvatarCache
from fishinglive_bridge.config import Settings, load_settings
from fishinglive_bridge.host_state import HostState
from fishinglive_bridge.mirror import UpstreamMirror
from fishinglive_bridge.normalize import EventIdFactory, normalize_comment, normalize_gift
from fishinglive_bridge.queue_store import EventQueue
from fishinglive_bridge.redact import RedactingFormatter
from fishinglive_bridge.samples import interaction_batch, join_batch
from fishinglive_bridge.schema import parse_event


INGEST = "ingest-key-0123456789"
READ = "read-key-0123456789"
# The TikTokLive check reads the installed package, so it only runs where the
# bridge venv actually has TikTokLive installed. Skipping is reported, not hidden.
HAS_TIKTOKLIVE = importlib.util.find_spec("TikTokLive") is not None


class User:
    def __init__(self, unique_id: str, nickname: str, user_id: int = 10) -> None:
        self.unique_id = unique_id
        self.display_id = unique_id
        self.nickname = nickname
        self.id = user_id


class Common:
    def __init__(self, msg_id: int, create_time: int) -> None:
        self.msg_id = msg_id
        self.create_time = create_time


class LiveEvent:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def settings(path: Path) -> Settings:
    return Settings(
        mode="mock",
        host="127.0.0.1",
        port=8787,
        ingest_key=INGEST,
        read_key=READ,
        queue_path=path,
        ssl_cert="",
        ssl_key="",
        tiktok_username="",
        session_id="",
        target_idc="",
    )


class BridgeTests(unittest.TestCase):
    def test_render_proxy_configuration(self) -> None:
        env = {
            "RENDER": "true",
            "PORT": "10000",
            "BRIDGE_MODE": "mock",
            "BRIDGE_INGEST_KEY": INGEST,
            "BRIDGE_READ_KEY": READ,
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_settings()
        self.assertEqual(cfg.host, "0.0.0.0")
        self.assertEqual(cfg.port, 10000)
        self.assertFalse(cfg.live)
        self.assertEqual(cfg.ssl_cert, "")

    def test_non_render_public_bind_still_needs_tls(self) -> None:
        env = {
            "BRIDGE_HOST": "0.0.0.0",
            "BRIDGE_MODE": "mock",
            "BRIDGE_INGEST_KEY": INGEST,
            "BRIDGE_READ_KEY": READ,
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "public bind requires TLS"):
                load_settings()

    def test_local_defaults_unchanged(self) -> None:
        env = {
            "BRIDGE_MODE": "mock",
            "BRIDGE_INGEST_KEY": INGEST,
            "BRIDGE_READ_KEY": READ,
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_settings()
        self.assertEqual((cfg.host, cfg.port), ("127.0.0.1", 8787))

    @unittest.skipUnless(HAS_TIKTOKLIVE, "TikTokLive 7.0.1 chưa cài trong virtualenv này.")
    def test_installed_tiktoklive_api(self) -> None:
        from TikTokLive.events import CommentEvent, FollowEvent, GiftEvent, LikeEvent

        self.assertEqual(metadata.version("TikTokLive"), "7.0.1")
        self.assertIsInstance(CommentEvent.comment, property)
        self.assertIsInstance(FollowEvent, type)
        self.assertIsInstance(LikeEvent, type)
        self.assertIsInstance(GiftEvent.streaking, property)

    def test_schema_rejects_paid_and_bad_shape(self) -> None:
        event, reason = parse_event({
            "eventId": "gift-paid",
            "type": "gift",
            "viewerId": "v-01",
            "nickname": "Hà",
            "timestamp": 10,
            "payload": {"amount": 1, "productId": 9},
        })
        self.assertIsNone(event)
        self.assertEqual(reason, "paid_reward")
        event, reason = parse_event({"eventId": "bad", "type": "comment"})
        self.assertIsNone(event)
        self.assertEqual(reason, "invalid")

    def test_streak_is_emitted_once_and_missing_id_is_stable(self) -> None:
        factory = EventIdFactory(window_seconds=2)
        user = User("viewer", "Lan")
        common = Common(0, 1_700_000_000)
        skipped = normalize_gift(LiveEvent(
            user=user, common=common, streaking=True, repeat_count=3, repeat_end=0, group_id=44,
        ), factory)
        final = normalize_gift(LiveEvent(
            user=user, common=common, streaking=False, repeat_count=7, repeat_end=1, group_id=44,
        ), factory)
        again = normalize_gift(LiveEvent(
            user=user, common=common, streaking=False, repeat_count=7, repeat_end=1, group_id=44,
        ), factory)
        self.assertIsNone(skipped)
        self.assertIsNotNone(final)
        self.assertEqual(final["payload"]["amount"], 7)
        self.assertEqual(final["eventId"], again["eventId"])
        comment = normalize_comment(LiveEvent(
            user=user, common=Common(501, 1_700_000_000), comment="cau",
        ), factory)
        self.assertEqual(comment["eventId"], "c-501")
        self.assertEqual(comment["payload"]["text"], "cau")

    def test_queue_holds_events_until_ack_and_stays_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue.json"
            clock = {"now": 1_000.0}
            queue = EventQueue(path, now=lambda: clock["now"])
            session_id = queue.status()["sessionId"]
            started = time.perf_counter()
            created = []
            for event in join_batch():
                status, sequence = queue.publish(event, rate_limit=False)
                self.assertEqual(status, "created")
                created.append(sequence)
            status, rows = queue.pull(20)
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(f"LATENCY_MS queue_publish_and_pull={elapsed_ms:.3f}")
            self.assertEqual(status, "ok")
            self.assertEqual(len(rows), 15)
            self.assertLess(elapsed_ms, 2000)
            self.assertEqual([row["sequence"] for row in rows], created)
            self.assertFalse(path.exists())
            dropped = queue.note_transport_drop()
            self.assertEqual(dropped["sessionId"], session_id)
            self.assertEqual(dropped["depth"], 15)
            status, again = queue.pull(20)
            self.assertEqual([row["sequence"] for row in again], created)
            fresh = EventQueue()
            self.assertEqual(fresh.pull(20)[1], [])
            self.assertNotEqual(fresh.status()["sessionId"], session_id)
            status, removed = queue.ack(created)
            self.assertEqual(removed, 15)
            self.assertEqual(queue.pull(20)[1], [])
            status, sequence = queue.publish(join_batch()[0], rate_limit=False)
            self.assertEqual(status, "duplicate")
            self.assertEqual(sequence, created[0])
            self.assertEqual(queue.pull(20)[1], [])

    def test_api_auth_batch_dedup_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue.json"
            queue = EventQueue(path)
            client = TestClient(create_app(settings(path), queue))
            denied = client.get("/v1/events")
            self.assertEqual(denied.status_code, 401)
            wrong = client.post(
                "/v1/events",
                headers={"X-Bridge-Key": READ},
                json=join_batch()[0],
            )
            self.assertEqual(wrong.status_code, 401)
            body = join_batch() + interaction_batch()
            body.append({
                "eventId": "bad-shape",
                "type": "comment",
                "viewerId": "v-01",
                "nickname": "Hà",
                "timestamp": 10,
            })
            started = time.perf_counter()
            posted = client.post("/v1/events", headers={"X-Bridge-Key": INGEST}, json={"events": body})
            self.assertEqual(posted.status_code, 200)
            results = posted.json()["results"]
            self.assertEqual(results[-1]["status"], "invalid")
            self.assertEqual(sum(1 for row in results if row["status"] == "created"), 18)
            pulled = client.get("/v1/events?limit=20", headers={"X-Bridge-Key": READ})
            http_ms = (time.perf_counter() - started) * 1000
            print(f"LATENCY_MS api_post_and_pull={http_ms:.3f}")
            self.assertEqual(pulled.status_code, 200)
            self.assertEqual(len(pulled.json()["events"]), 18)
            self.assertLess(http_ms, 2000)
            repeat = client.get("/v1/events?limit=20", headers={"X-Bridge-Key": READ})
            self.assertEqual(len(repeat.json()["events"]), 18)
            sequences = [row["sequence"] for row in pulled.json()["events"]]
            acked = client.post(
                "/v1/events/ack",
                headers={"X-Bridge-Key": READ},
                json={"sequences": sequences},
            )
            self.assertEqual(acked.json()["acked"], 18)
            listed = client.get("/v1/events", headers={"X-Bridge-Key": READ}).json()
            self.assertEqual(listed["events"], [])
            self.assertTrue(listed["sessionId"])
            self.assertFalse(path.exists())
            denied_reset = client.post("/v1/session/reset", headers={"X-Bridge-Key": INGEST}, json={})
            self.assertEqual(denied_reset.status_code, 400)
            duplicate = client.post(
                "/v1/events",
                headers={"X-Bridge-Key": INGEST},
                json=join_batch()[0],
            )
            self.assertEqual(duplicate.json()["results"][0]["status"], "duplicate")
            before = client.get("/v1/health").json()["sessionId"]
            reset = client.post(
                "/v1/session/reset",
                headers={"X-Bridge-Key": INGEST},
                json={"confirm": True},
            )
            self.assertEqual(reset.status_code, 200)
            self.assertNotEqual(reset.json()["sessionId"], before)
            self.assertEqual(client.get("/v1/events", headers={"X-Bridge-Key": READ}).json()["events"], [])

    def test_roblox_read_header_is_x_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue.json"
            queue = EventQueue(path)
            client = TestClient(create_app(settings(path), queue))
            event = join_batch()[0]
            denied_get = client.get("/v1/events", headers={"x-api-key": "wrong-key-0123456789"})
            self.assertEqual(denied_get.status_code, 401)
            self.assertNotIn(READ, denied_get.text)
            self.assertNotIn(INGEST, denied_get.text)
            ingest_as_read = client.get("/v1/events", headers={"x-api-key": INGEST})
            self.assertEqual(ingest_as_read.status_code, 401)
            read_as_ingest = client.post("/v1/events", headers={"x-api-key": INGEST}, json=event)
            self.assertEqual(read_as_ingest.status_code, 401)
            posted = client.post("/v1/events", headers={"X-Bridge-Key": INGEST}, json=event)
            self.assertEqual(posted.status_code, 200)
            self.assertEqual(posted.json()["results"][0]["status"], "created")
            held = client.get("/v1/events?limit=1", headers={"x-api-key": READ})
            self.assertEqual(held.status_code, 200)
            self.assertEqual(len(held.json()["events"]), 1)
            again = client.get("/v1/events?limit=1", headers={"x-api-key": READ})
            self.assertEqual(again.json()["events"][0]["sequence"], held.json()["events"][0]["sequence"])
            sequence = held.json()["events"][0]["sequence"]
            bad_ack = client.post(
                "/v1/events/ack",
                headers={"x-api-key": "wrong-key-0123456789"},
                json={"sequences": [sequence]},
            )
            self.assertEqual(bad_ack.status_code, 401)
            self.assertEqual(len(client.get("/v1/events", headers={"x-api-key": READ}).json()["events"]), 1)
            acked = client.post(
                "/v1/events/ack",
                headers={"x-api-key": READ},
                json={"sequences": [sequence]},
            )
            self.assertEqual(acked.status_code, 200)
            self.assertEqual(acked.json()["acked"], 1)
            self.assertEqual(client.get("/v1/events", headers={"x-api-key": READ}).json()["events"], [])
            duplicate = client.post("/v1/events", headers={"X-Bridge-Key": INGEST}, json=event)
            self.assertEqual(duplicate.json()["results"][0]["status"], "duplicate")
            self.assertEqual(client.get("/v1/events", headers={"x-api-key": READ}).json()["events"], [])

    def test_overload_merges_likes_and_keeps_gifts_and_joins(self) -> None:
        queue = EventQueue(capacity=2)

        def like(index: int, viewer: str) -> dict:
            return {
                "eventId": f"like-{index}",
                "type": "like",
                "viewerId": viewer,
                "nickname": "Lan",
                "timestamp": 1_700_000_000 + index,
                "payload": {},
            }

        self.assertEqual(queue.publish(like(1, "v-01"), rate_limit=False)[0], "created")
        self.assertEqual(queue.publish(like(2, "v-02"), rate_limit=False)[0], "created")
        status, sequence = queue.publish(like(3, "v-01"), rate_limit=False)
        self.assertEqual(status, "coalesced")
        self.assertEqual(queue.status()["depth"], 2)
        _status, rows = queue.pull(20)
        merged = [row for row in rows if row["sequence"] == sequence][0]
        self.assertEqual(merged["event"]["payload"]["merged"], True)
        self.assertEqual(merged["event"]["payload"]["count"], 2)
        gift = {
            "eventId": "gift-keep",
            "type": "gift",
            "viewerId": "v-01",
            "nickname": "Lan",
            "timestamp": 1_700_000_100,
            "payload": {"amount": 1},
        }
        self.assertEqual(queue.publish(gift, rate_limit=False)[0], "created")
        chat = {
            "eventId": "chat-drop",
            "type": "comment",
            "viewerId": "v-03",
            "nickname": "Lan",
            "timestamp": 1_700_000_101,
            "payload": {"text": "xin chao"},
        }
        self.assertEqual(queue.publish(chat, rate_limit=False)[0], "overloaded")
        join = {
            "eventId": "join-keep",
            "type": "comment",
            "viewerId": "v-04",
            "nickname": "Lan",
            "timestamp": 1_700_000_102,
            "payload": {"text": "cau"},
        }
        self.assertEqual(queue.publish(join, rate_limit=False)[0], "created")
        self.assertGreater(queue.status()["depth"], 3)
        kinds = [row["event"]["type"] for row in queue.pull(20)[1]]
        self.assertIn("gift", kinds)
        self.assertEqual(sum(1 for kind in kinds if kind == "comment"), 1)

    def test_avatar_cache_is_64_and_memory_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            before = set(Path(folder).iterdir())
            cache = AvatarCache(limit=2)
            wide = bytes([index % 256 for index in range(8 * 8 * 4)])
            first = cache.store("v-01", wide, 8, 8)
            second_pixels = bytes([(index + 3) % 256 for index in range(8 * 8 * 4)])
            again = cache.store("v-01", second_pixels, 8, 8)
            self.assertIs(first, again)
            self.assertEqual(cache.stores, 1)
            self.assertEqual(first[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", first[16:24]), (64, 64))
            cache.store("v-02", wide, 8, 8)
            cache.store("v-03", wide, 8, 8)
            self.assertIsNone(cache.get("v-01"))
            self.assertIsNotNone(cache.get("v-03"))
            self.assertEqual(set(Path(folder).iterdir()), before)
            cache.note_join("v-04")
            self.assertEqual(cache.stores, 3)

    def test_local_control_stays_on_loopback_and_keeps_queue_rules(self) -> None:
        page = Path(__file__).resolve().parents[2] / "fishinglive-simulator"
        with tempfile.TemporaryDirectory() as folder:
            queue = EventQueue()
            before = queue.status()["sessionId"]
            app = create_app(settings(Path(folder) / "queue.json"), queue, control_dir=page)
            outsider = TestClient(app)
            self.assertEqual(outsider.get("/control").status_code, 404)
            local = TestClient(app, client=("127.0.0.1", 50000))
            page_response = local.get("/control")
            self.assertEqual(page_response.status_code, 200)
            self.assertNotIn(INGEST, page_response.text)
            self.assertNotIn(READ, page_response.text)
            self.assertNotIn("localStorage.setItem", page_response.text)
            self.assertNotIn("sessionStorage.setItem", page_response.text)
            self.assertNotIn("indexedDB", page_response.text)
            event = {
                "eventId": "control-join-01",
                "type": "comment",
                "viewerId": "test_001",
                "nickname": "Nguyễn Văn A",
                "timestamp": 1_700_000_111,
                "payload": {"text": "cau"},
            }
            posted = local.post("/control/events", json=event)
            self.assertEqual(posted.status_code, 200)
            self.assertEqual(posted.json()["status"], "created")
            self.assertNotIn(INGEST, posted.text)
            again = local.post("/control/events", json=event)
            self.assertEqual(again.json()["status"], "duplicate")
            self.assertEqual(queue.status()["sessionId"], before)
            self.assertEqual(queue.status()["depth"], 1)
            pulled = local.get("/v1/events", headers={"x-api-key": READ})
            self.assertEqual(pulled.status_code, 200)
            self.assertEqual(pulled.json()["events"][0]["event"]["eventId"], "control-join-01")
            status = local.get("/control/status")
            self.assertEqual(status.json()["mode"], "mock")
            self.assertNotIn("ingest_key", status.text)
            live = settings(Path(folder) / "queue.json")
            live = Settings(
                mode="live",
                host="127.0.0.1",
                port=8787,
                ingest_key=INGEST,
                read_key=READ,
                queue_path=Path(folder) / "queue.json",
                ssl_cert="",
                ssl_key="",
                tiktok_username="host",
                session_id="",
                target_idc="",
            )
            blocked = TestClient(create_app(live, EventQueue(), control_dir=page), client=("127.0.0.1", 50000))
            self.assertEqual(blocked.post("/control/events", json=event).status_code, 409)
            public = settings(Path(folder) / "queue.json")
            public = Settings(
                mode="mock",
                host="203.0.113.10",
                port=8787,
                ingest_key=INGEST,
                read_key=READ,
                queue_path=Path(folder) / "queue.json",
                ssl_cert="cert.pem",
                ssl_key="key.pem",
                tiktok_username="",
                session_id="",
                target_idc="",
            )
            hidden = TestClient(create_app(public, EventQueue(), control_dir=page), client=("127.0.0.1", 50000))
            self.assertEqual(hidden.get("/control").status_code, 404)
            self.assertEqual(hidden.post("/control/events", json=event).status_code, 404)

    def test_host_controller_is_local_and_honest(self) -> None:
        page = Path(__file__).resolve().parents[1] / "host-controller"
        with tempfile.TemporaryDirectory() as folder:
            queue = EventQueue()
            app = create_app(settings(Path(folder) / "queue.json"), queue, host_dir=page)
            outsider = TestClient(app)
            self.assertEqual(outsider.get("/host").status_code, 404)
            local = TestClient(app, client=("127.0.0.1", 50000))
            headers = {"Host": "127.0.0.1:8787"}
            tunneled = local.get("/host", headers={**headers, "CF-Connecting-IP": "203.0.113.8"})
            self.assertEqual(tunneled.status_code, 404)
            opened = local.get("/host", headers=headers)
            self.assertEqual(opened.status_code, 200)
            script = local.get("/host/app.js", headers=headers).text
            style = local.get("/host/styles.css", headers=headers).text
            blob = opened.text + script + style
            self.assertNotIn(INGEST, blob)
            self.assertNotIn(READ, blob)
            self.assertNotIn("localStorage.setItem", blob)
            self.assertNotIn("BRIDGE_INGEST_KEY=", blob)
            session = local.get("/host/session", headers=headers)
            self.assertEqual(session.status_code, 200)
            token = session.json()["csrf"]
            self.assertNotIn(token, opened.text)
            auth = {**headers, "Origin": "http://127.0.0.1:8787", "X-CSRF-Token": token}
            self.assertEqual(local.post("/host/action", headers=headers, json={"action": "add_viewer", "username": "host_a"}).status_code, 403)
            evil = local.post("/host/action", headers={**auth, "Origin": "http://evil.example"}, json={"action": "add_viewer", "username": "host_b"})
            self.assertEqual(evil.status_code, 403)
            board = local.get("/host/dashboard", headers=headers).json()
            self.assertEqual(board["roblox"]["label"], "ROBLOX DISCONNECTED")
            self.assertIsNone(board["totalFish"])
            self.assertIsNone(board["activeNpc"])
            created = local.post("/host/action", headers=auth, json={"action": "add_viewer", "username": "host_a"})
            self.assertEqual(created.status_code, 200)
            self.assertEqual(created.json()["bridgeStatus"], "QUEUED")
            self.assertNotEqual(created.json()["robloxStatus"], "ACKNOWLEDGED")
            self.assertEqual(queue.status()["depth"], 1)
            likes = local.post("/host/action", headers=auth, json={"action": "like", "username": "host_a", "count": 499, "eventId": "like-499"})
            self.assertFalse(likes.json()["viewer"]["fishingUnlocked"])
            self.assertEqual(likes.json()["viewer"]["likes"], 499)
            again = local.post("/host/action", headers=auth, json={"action": "like", "username": "host_a", "count": 499, "eventId": "like-499"})
            self.assertEqual(again.json()["bridgeStatus"], "ACCEPTED_BY_BRIDGE")
            self.assertEqual(again.json()["viewer"]["likes"], 499)
            unlocked = local.post("/host/action", headers=auth, json={"action": "like", "username": "host_a", "count": 1, "eventId": "like-500"})
            self.assertTrue(unlocked.json()["viewer"]["fishingUnlocked"])
            still = local.post("/host/action", headers=auth, json={"action": "like", "username": "host_a", "count": 1, "eventId": "like-501"})
            self.assertEqual(still.json()["viewer"]["likes"], 501)
            self.assertTrue(still.json()["viewer"]["fishingUnlocked"])
            for text in ("câu", "/giật", "/vitri"):
                posted = local.post("/host/action", headers=auth, json={"action": "comment", "username": "host_a", "text": text})
                self.assertEqual(posted.json()["bridgeStatus"], "QUEUED", text)
            steal = local.post("/host/action", headers=auth, json={
                "action": "gift", "username": "host_a", "purpose": "steal",
                "giftId": "GIFT_STEAL", "giftName": "Rose", "coinValue": 20, "repeatCount": 1,
            })
            camera_gift = local.post("/host/action", headers=auth, json={
                "action": "gift", "username": "host_a", "purpose": "camera",
                "giftId": "GIFT_CAMERA", "giftName": "Galaxy", "coinValue": 30, "repeatCount": 1,
            })
            self.assertEqual(steal.json()["bridgeStatus"], "QUEUED")
            self.assertEqual(camera_gift.json()["bridgeStatus"], "QUEUED")
            baits = local.get("/host/baits", headers=headers).json()
            self.assertEqual([row["coins"] for row in baits["tiers"]], [10, 30, 50, 100, 200])
            self.assertFalse(baits["approvedForLive"])
            forced = local.post("/host/action", headers=auth, json={"action": "force", "kind": "ForceFishSuccess", "username": "host_a"})
            self.assertFalse(forced.json()["appliedOnRoblox"])
            self.assertNotEqual(forced.json()["robloxStatus"], "ACKNOWLEDGED")
            pulled = local.get("/v1/events?limit=20", headers={"x-api-key": READ})
            self.assertEqual(pulled.status_code, 200)
            sequences = [row["sequence"] for row in pulled.json()["events"]]
            self.assertTrue(sequences)
            connected = local.get("/host/dashboard", headers=headers).json()
            self.assertEqual(connected["roblox"]["label"], "ROBLOX CONNECTED")
            self.assertEqual(connected["roblox"]["acks"], 0)
            acked = local.post("/v1/events/ack", headers={"x-api-key": READ}, json={"sequences": sequences})
            self.assertEqual(acked.json()["acked"], len(sequences))
            log = local.get("/host/events", headers=headers).json()["events"]
            acknowledged = [row for row in log if row["sequence"] in sequences]
            self.assertTrue(acknowledged)
            self.assertTrue(all(row["robloxStatus"] == "ACKNOWLEDGED" for row in acknowledged))
            self.assertNotIn(INGEST, str(log))
            self.assertNotIn(READ, str(log))
            live = Settings(
                mode="live",
                host="127.0.0.1",
                port=8787,
                ingest_key=INGEST,
                read_key=READ,
                queue_path=Path(folder) / "queue.json",
                ssl_cert="",
                ssl_key="",
                tiktok_username="host",
                session_id="",
                target_idc="",
            )
            live_app = create_app(live, EventQueue(), host_dir=page)
            live_client = TestClient(live_app, client=("127.0.0.1", 50000))
            live_token = live_client.get("/host/session", headers=headers).json()["csrf"]
            blocked = live_client.post(
                "/host/action",
                headers={**auth, "X-CSRF-Token": live_token},
                json={"action": "force", "kind": "ForceFishSuccess", "username": "host_a"},
            )
            self.assertEqual(blocked.status_code, 409)
            app.state.host._stress_loop("overload", 0)
            self.assertLessEqual(queue.status()["depth"], 2000)
            self.assertEqual(queue.status()["depth"], 2000)

    def test_host_stress_stop(self) -> None:
        page = Path(__file__).resolve().parents[1] / "host-controller"
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(settings(Path(folder) / "queue.json"), EventQueue(), host_dir=page)
            local = TestClient(app, client=("127.0.0.1", 50000))
            headers = {"Host": "127.0.0.1:8787", "Origin": "http://127.0.0.1:8787"}
            token = local.get("/host/session", headers=headers).json()["csrf"]
            headers["X-CSRF-Token"] = token
            started = local.post("/host/action", headers=headers, json={"action": "stress", "kind": "join500"})
            self.assertEqual(started.status_code, 200)
            stopped = local.post("/host/action", headers=headers, json={"action": "stress_stop"})
            self.assertEqual(stopped.status_code, 200)
            self.assertLessEqual(app.state.host.queue.status()["depth"], 2000)
            time.sleep(0.2)
            self.assertFalse(app.state.host.stress["running"])

    def test_host_auto_demo_ten_viewers_and_stop(self) -> None:
        page = Path(__file__).resolve().parents[1] / "host-controller"
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(settings(Path(folder) / "queue.json"), EventQueue(), host_dir=page)
            local = TestClient(app, client=("127.0.0.1", 50000))
            headers = {"Host": "127.0.0.1:8787", "Origin": "http://127.0.0.1:8787"}
            token = local.get("/host/session", headers=headers).json()["csrf"]
            headers["X-CSRF-Token"] = token
            self.assertIn("auto-demo-start", local.get("/host", headers=headers).text)
            started = local.post("/host/action", headers=headers, json={"action": "auto_demo_start"})
            self.assertEqual(started.status_code, 200)
            self.assertEqual(len(started.json()["viewers"]), 10)
            self.assertFalse(started.json()["appliedOnRoblox"])
            state = local.get("/host/auto-demo", headers=headers).json()
            self.assertTrue(state["running"])
            self.assertEqual(len(state["viewers"]), 10)
            deadline = time.time() + 6
            while time.time() < deadline and app.state.host.demo["sent"] < 3:
                time.sleep(0.1)
            self.assertGreaterEqual(app.state.host.demo["sent"], 3)
            board = local.get("/host/dashboard", headers=headers).json()
            self.assertGreaterEqual(board["viewers"], 10)
            self.assertGreater(board["totalLikes"], 0)
            self.assertTrue(board["autoDemo"]["running"])
            busy = local.post("/host/action", headers=headers, json={"action": "auto_demo_start"})
            self.assertFalse(busy.json()["ok"])
            stopped = local.post("/host/action", headers=headers, json={"action": "auto_demo_stop"})
            self.assertEqual(stopped.status_code, 200)
            time.sleep(0.3)
            self.assertFalse(app.state.host.demo["running"])
            self.assertLessEqual(app.state.host.queue.status()["depth"], 2000)

    def test_ack_carries_telemetry_for_host_page(self) -> None:
        page = Path(__file__).resolve().parents[1] / "host-controller"
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(settings(Path(folder) / "queue.json"), EventQueue(), host_dir=page)
            local = TestClient(app, client=("127.0.0.1", 50000))
            headers = {"Host": "127.0.0.1:8787"}
            self.assertEqual(local.get("/v1/telemetry").status_code, 401)
            self.assertEqual(local.get("/v1/telemetry", headers={"x-api-key": "wrong-key-000000"}).status_code, 401)
            acked = local.post(
                "/v1/events/ack",
                headers={"x-api-key": READ},
                json={
                    "sequences": [],
                    "telemetry": {
                        "fps": 59.94,
                        "physicsFps": 60,
                        "ramMb": 812.5,
                        "players": 1,
                        "jobId": "job-1",
                        "secret": "SHOULD_NOT_LEAK",
                        "ramMbShadow": -5,
                    },
                },
            )
            self.assertEqual(acked.status_code, 200)
            self.assertEqual(acked.json()["acked"], 0)
            # Roblox keeps using x-api-key. The mirror holds the ingest key, so both pass.
            for key in (READ, INGEST):
                row = local.get("/v1/telemetry", headers={"x-api-key": key})
                self.assertEqual(row.status_code, 200)
                body = row.json()
                self.assertEqual(body["telemetry"]["fps"], 59.94)
                self.assertEqual(body["telemetry"]["jobId"], "job-1")
                self.assertTrue(body["fresh"])
                self.assertEqual(body["depth"], 0)
                self.assertNotIn("secret", str(body))
                self.assertNotIn("ramMbShadow", str(body))
                self.assertNotIn(READ, str(body))
                self.assertNotIn(INGEST, str(body))
            # Out-of-range frames are dropped, the last good snapshot stays.
            self.assertEqual(
                local.post(
                    "/v1/events/ack",
                    headers={"x-api-key": READ},
                    json={"sequences": [], "telemetry": {"fps": 9999, "ramMb": "high"}},
                ).status_code,
                200,
            )
            kept = local.get("/v1/telemetry", headers={"x-api-key": READ}).json()
            self.assertEqual(kept["telemetry"]["fps"], 59.94)
            self.assertEqual(kept["telemetry"]["ramMb"], 812.5)
            board = local.get("/host/dashboard", headers=headers).json()
            self.assertEqual(board["roblox"]["telemetrySource"], "local")
            self.assertTrue(board["roblox"]["telemetryFresh"])
            self.assertEqual(board["roblox"]["telemetry"]["ramMb"], 812.5)
            scoped = local.get("/host/telemetry", headers=headers)
            self.assertEqual(scoped.status_code, 200)
            self.assertEqual(scoped.json()["telemetry"]["fps"], 59.94)
            self.assertTrue(scoped.json()["fresh"])
            self.assertNotIn(INGEST, scoped.text)

    def test_live_settings_draft_never_enables_live(self) -> None:
        page = Path(__file__).resolve().parents[1] / "host-controller"
        with tempfile.TemporaryDirectory() as folder:
            draft_path = Path(folder) / "live-settings.json"
            cfg = replace(settings(Path(folder) / "queue.json"), live_settings_path=str(draft_path))
            app = create_app(cfg, EventQueue(), host_dir=page)
            local = TestClient(app, client=("127.0.0.1", 50000))
            headers = {"Host": "127.0.0.1:8787", "Origin": "http://127.0.0.1:8787"}
            token = local.get("/host/session", headers=headers).json()["csrf"]
            auth = {**headers, "X-CSRF-Token": token}
            opened = local.get("/host/live-settings", headers=headers)
            self.assertEqual(opened.status_code, 200)
            self.assertEqual(opened.json()["draft"], {})
            self.assertTrue(opened.json()["mockMode"])
            self.assertFalse(opened.json()["savedSettingsEnabled"])
            self.assertFalse(opened.json()["liveAllowed"])
            saved = local.post(
                "/host/action",
                headers=auth,
                json={
                    "action": "save_live_settings",
                    "tiktokUsername": "@studio_host",
                    "mode": "live",
                    "bridgeUrl": "https://python-bridge-6v9w.onrender.com/",
                },
            )
            self.assertEqual(saved.status_code, 200)
            body = saved.json()
            self.assertTrue(body["ok"])
            self.assertFalse(body["applied"])
            self.assertTrue(body["liveBlocked"])
            self.assertIn("LIVE", body["warning"])
            self.assertEqual(body["draft"]["tiktokUsername"], "studio_host")
            self.assertEqual(body["draft"]["bridgeUrl"], "https://python-bridge-6v9w.onrender.com")
            self.assertTrue(draft_path.is_file())
            stored = draft_path.read_text(encoding="utf-8")
            self.assertEqual(json.loads(stored)["mode"], "live")
            self.assertNotIn(INGEST, stored)
            self.assertNotIn(READ, stored)
            state = local.get("/host/live-settings", headers=headers).json()
            self.assertEqual(state["draft"]["mode"], "live")
            self.assertEqual(state["mode"], "mock")
            self.assertTrue(state["mockMode"])
            self.assertFalse(state["savedSettingsEnabled"])
            self.assertFalse(state["liveAllowed"])
            board = local.get("/host/dashboard", headers=headers).json()
            self.assertTrue(board["mockMode"])
            self.assertEqual(board["bridge"]["mode"], "mock")
            bad_name = local.post(
                "/host/action",
                headers=auth,
                json={"action": "save_live_settings", "tiktokUsername": "a b", "mode": "mock", "bridgeUrl": ""},
            ).json()
            self.assertFalse(bad_name["ok"])
            self.assertEqual(bad_name["error"], "invalid")
            bad_mode = local.post(
                "/host/action",
                headers=auth,
                json={"action": "save_live_settings", "tiktokUsername": "studio_host", "mode": "turbo", "bridgeUrl": ""},
            ).json()
            self.assertFalse(bad_mode["ok"])
            bad_url = local.post(
                "/host/action",
                headers=auth,
                json={"action": "save_live_settings", "tiktokUsername": "studio_host", "mode": "mock", "bridgeUrl": "ftp://x"},
            ).json()
            self.assertFalse(bad_url["ok"])
            refresh = local.post("/host/action", headers=auth, json={"action": "remote_refresh"}).json()
            self.assertTrue(refresh["ok"])
            self.assertFalse(refresh["render"]["enabled"])
            self.assertIsNone(local.get("/host/report", headers=headers).json()["report"])

    def test_host_test_suite_report_is_honest(self) -> None:
        page = Path(__file__).resolve().parents[1] / "host-controller"
        with tempfile.TemporaryDirectory() as folder:
            draft_path = Path(folder) / "live-settings.json"
            cfg = replace(settings(Path(folder) / "queue.json"), live_settings_path=str(draft_path))
            queue = EventQueue()
            app = create_app(cfg, queue, host_dir=page)
            local = TestClient(app, client=("127.0.0.1", 50000))
            headers = {"Host": "127.0.0.1:8787", "Origin": "http://127.0.0.1:8787"}
            token = local.get("/host/session", headers=headers).json()["csrf"]
            auth = {**headers, "X-CSRF-Token": token}
            ran = local.post("/host/action", headers=auth, json={"action": "run_test_suite"})
            self.assertEqual(ran.status_code, 200)
            result = ran.json()
            self.assertEqual(result["bridgeStatus"], "ACCEPTED_BY_BRIDGE")
            self.assertEqual(result["robloxStatus"], "NOT_DELIVERED")
            report = result["report"]
            self.assertEqual(report["result"], "PASS")
            self.assertEqual(report["failed"], 0)
            self.assertTrue(report["mockOnly"])
            self.assertEqual(report["total"], len(report["items"]))
            self.assertEqual(report["passed"] + report["failed"] + report["warned"], report["total"])
            ids = [row["id"] for row in report["items"]]
            for key in (
                "viewer", "like500", "unlock500", "follow", "cau", "giat", "vitri",
                "gift_camera", "gift_steal", "baits", "pull10",
                "camera_diorama", "camera_focus", "camera_showcase", "duplicate",
                "auto_demo", "queue", "mirror", "render", "roblox_ack",
            ):
                self.assertIn(key, ids)
            rows = {row["id"]: row for row in report["items"]}
            self.assertEqual({row["status"] for row in report["items"]} - {"PASS", "FAIL", "WARN"}, set())
            self.assertEqual(rows["duplicate"]["status"], "PASS")
            self.assertEqual(rows["roblox_ack"]["status"], "WARN")
            self.assertEqual(rows["roblox_ack"]["robloxStatus"], "NOT_DELIVERED")
            self.assertEqual(rows["mirror"]["status"], "WARN")
            self.assertEqual(rows["queue"]["status"], "PASS")
            self.assertNotIn("ACKNOWLEDGED", str(report))
            served = local.get("/host/report", headers=headers).json()["report"]
            self.assertEqual(served["generatedAt"], report["generatedAt"])
            self.assertEqual(local.get("/host/dashboard", headers=headers).json()["report"]["result"], "PASS")
            # The ten demo viewers are removed again; only the suite's own viewer stays.
            self.assertEqual(local.get("/host/dashboard", headers=headers).json()["viewers"], 1)
            self.assertLess(queue.status()["depth"], queue.capacity)
            seed = local.post("/host/action", headers=auth, json={"action": "seed_fishers", "count": 3}).json()
            self.assertTrue(seed["ok"])
            self.assertEqual(seed["created"], 3)
            self.assertEqual(len(seed["fishers"]), 3)
            self.assertFalse(seed["appliedOnRoblox"])
            fishers = local.get("/host/viewers", headers=headers, params={"q": "", "offset": 0}).json()
            self.assertGreaterEqual(fishers["total"], 4)
            self.assertFalse(local.post("/host/action", headers=auth, json={"action": "seed_fishers", "count": 11}).json()["ok"])

    def test_auto_demo_sizes_and_live_gate(self) -> None:
        page = Path(__file__).resolve().parents[1] / "host-controller"
        with tempfile.TemporaryDirectory() as folder:
            cfg = replace(settings(Path(folder) / "queue.json"), live_settings_path=str(Path(folder) / "live.json"))
            app = create_app(cfg, EventQueue(), host_dir=page)
            local = TestClient(app, client=("127.0.0.1", 50000))
            headers = {"Host": "127.0.0.1:8787", "Origin": "http://127.0.0.1:8787"}
            token = local.get("/host/session", headers=headers).json()["csrf"]
            auth = {**headers, "X-CSRF-Token": token}
            big = local.post("/host/action", headers=auth, json={"action": "auto_demo_start", "viewers": 100})
            self.assertEqual(big.status_code, 200)
            body = big.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["requested"], 100)
            self.assertEqual(body["joinPending"], 90)
            self.assertEqual(len(body["viewers"]), 10)
            state = local.get("/host/auto-demo", headers=headers).json()
            self.assertTrue(state["running"])
            self.assertEqual(state["requested"], 100)
            self.assertLessEqual(state["joinPending"], 90)
            self.assertGreaterEqual(state["joinPending"], 0)
            busy = local.post("/host/action", headers=auth, json={"action": "auto_demo_start", "viewers": 500}).json()
            self.assertFalse(busy["ok"])
            stopped = local.post("/host/action", headers=auth, json={"action": "auto_demo_stop"})
            self.assertEqual(stopped.status_code, 200)
            time.sleep(0.2)
            self.assertFalse(local.get("/host/auto-demo", headers=headers).json()["running"])
            invalid = local.post("/host/action", headers=auth, json={"action": "auto_demo_start", "viewers": 55}).json()
            self.assertFalse(invalid["ok"])
            self.assertEqual(invalid["error"], "invalid")
            live = replace(cfg, mode="live", tiktok_username="host")
            live_app = create_app(live, EventQueue(), host_dir=page)
            live_client = TestClient(live_app, client=("127.0.0.1", 50000))
            live_token = live_client.get("/host/session", headers=headers).json()["csrf"]
            live_auth = {**headers, "X-CSRF-Token": live_token}
            self.assertEqual(live_client.post("/host/action", headers=live_auth, json={"action": "run_test_suite"}).status_code, 409)
            self.assertEqual(live_client.post("/host/action", headers=live_auth, json={"action": "auto_demo_start", "viewers": 10}).status_code, 409)
            self.assertEqual(live_client.post("/host/action", headers=live_auth, json={"action": "seed_fishers", "count": 2}).status_code, 409)

    def test_source_cannot_send_chat(self) -> None:
        source = Path(__file__).parents[1] / "src" / "fishinglive_bridge" / "tiktok_source.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("send_room_chat(", text)
        self.assertIn("outbound TikTok chat is disabled", text)

    def test_logs_hide_secrets(self) -> None:
        formatter = RedactingFormatter([INGEST, "session-value-should-hide"], "%(message)s")
        record = logging_record(f"header {INGEST} cookie session-value-should-hide")
        rendered = formatter.format(record)
        self.assertNotIn(INGEST, rendered)
        self.assertNotIn("session-value-should-hide", rendered)
        self.assertIn("***", rendered)

    def test_public_bind_requires_tls(self) -> None:
        keys = ["BRIDGE_MODE", "BRIDGE_HOST", "BRIDGE_INGEST_KEY", "BRIDGE_READ_KEY", "BRIDGE_SSL_CERT", "BRIDGE_SSL_KEY"]
        saved = {key: os.environ.get(key) for key in keys}
        try:
            os.environ["BRIDGE_MODE"] = "mock"
            os.environ["BRIDGE_HOST"] = "203.0.113.10"
            os.environ["BRIDGE_INGEST_KEY"] = INGEST
            os.environ["BRIDGE_READ_KEY"] = READ
            os.environ["BRIDGE_SSL_CERT"] = ""
            os.environ["BRIDGE_SSL_KEY"] = ""
            with self.assertRaises(RuntimeError):
                load_settings(Path("does-not-exist.env"))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

class MirrorTests(unittest.TestCase):
    """The mock host page is loopback-only; the mirror is what Roblox can actually poll."""

    def mirror_settings(self, folder: Path, url: str = "", key: str = "") -> Settings:
        return Settings(
            mode="mock",
            host="127.0.0.1",
            port=8787,
            ingest_key=INGEST,
            read_key=READ,
            queue_path=folder / "queue.json",
            ssl_cert="",
            ssl_key="",
            tiktok_username="",
            session_id="",
            target_idc="",
            mirror_url=url,
            mirror_key=key,
        )

    def event(self, index: int) -> dict:
        return {
            "eventId": "mirror-%03d" % index,
            "type": "like",
            "viewerId": "mv%03d" % index,
            "nickname": "mv%03d" % index,
            "timestamp": 1000 + index,
            "payload": {"count": 1},
        }

    def test_mirror_off_by_default(self) -> None:
        env = {
            "BRIDGE_MODE": "mock",
            "BRIDGE_INGEST_KEY": INGEST,
            "BRIDGE_READ_KEY": READ,
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_settings(Path("does-not-exist.env"))
        self.assertEqual((cfg.mirror_url, cfg.mirror_key), ("", ""))

    def test_mirror_url_from_env_defaults_key_to_ingest(self) -> None:
        env = {
            "BRIDGE_MODE": "mock",
            "BRIDGE_INGEST_KEY": INGEST,
            "BRIDGE_READ_KEY": READ,
            "BRIDGE_MIRROR_URL": "https://python-bridge.invalid/",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_settings(Path("does-not-exist.env"))
        self.assertEqual(cfg.mirror_url, "https://python-bridge.invalid")
        self.assertEqual(cfg.mirror_key, INGEST)

    def test_mirror_url_must_be_http(self) -> None:
        env = {
            "BRIDGE_MODE": "mock",
            "BRIDGE_INGEST_KEY": INGEST,
            "BRIDGE_READ_KEY": READ,
            "BRIDGE_MIRROR_URL": "python-bridge.invalid",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "http"):
                load_settings(Path("does-not-exist.env"))

    def test_mirror_ignored_in_live_mode(self) -> None:
        env = {
            "BRIDGE_MODE": "live",
            "BRIDGE_INGEST_KEY": INGEST,
            "BRIDGE_READ_KEY": READ,
            "BRIDGE_MIRROR_URL": "https://python-bridge.invalid",
            "TIKTOK_USERNAME": "someone",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_settings(Path("does-not-exist.env"))
        self.assertTrue(cfg.live)
        self.assertEqual(cfg.mirror_url, "")
    def test_mirror_batches_twenty_and_counts(self) -> None:
        posted: list[int] = []

        def post(url, key, payload, timeout):
            posted.append(len(payload["events"]))
            return 200, json.dumps({"results": [{"status": "created"} for _ in payload["events"]]})

        mirror = UpstreamMirror("https://python-bridge.invalid", INGEST, post=post)
        try:
            for index in range(25):
                mirror.submit(self.event(index))
            self.assertTrue(mirror.wait_idle(5.0))
            status = mirror.status()
        finally:
            mirror.close()
        self.assertEqual(sum(posted), 25)
        self.assertLessEqual(max(posted), 20)
        self.assertEqual(status["sent"], 25)
        self.assertEqual(status["created"], 25)
        self.assertEqual(status["failed"], 0)
        self.assertEqual(status["queued"], 0)
        self.assertIsNotNone(status["latencyMs"])
        self.assertEqual(status["url"], "https://python-bridge.invalid/v1/events")

    def test_mirror_records_transport_failure(self) -> None:
        def post(url, key, payload, timeout):
            raise OSError("render unreachable")

        mirror = UpstreamMirror("https://python-bridge.invalid", INGEST, post=post)
        try:
            mirror.submit(self.event(1))
            deadline = time.time() + 8
            while time.time() < deadline and mirror.status()["failed"] == 0:
                time.sleep(0.05)
            status = mirror.status()
        finally:
            mirror.close()
        self.assertEqual(status["sent"], 0)
        self.assertEqual(status["failed"], 1)
        self.assertIn("render unreachable", status["lastError"])
    def test_host_publish_is_mirrored(self) -> None:
        posted: list[dict] = []

        def post(url, key, payload, timeout):
            posted.extend(payload["events"])
            return 200, json.dumps({"results": [{"status": "created"} for _ in payload["events"]]})

        mirror = UpstreamMirror("https://python-bridge.invalid", INGEST, post=post)
        try:
            with tempfile.TemporaryDirectory() as folder:
                host = HostState(EventQueue(), self.mirror_settings(Path(folder)), mirror)
                host.add_viewer("mirror_lead")
                liked = host.send_like("mirror_lead", 5, "mirror-like-1")
                self.assertEqual(liked["bridgeStatus"], "QUEUED")
                self.assertTrue(mirror.wait_idle(5.0))
                board = host.dashboard()
        finally:
            mirror.close()
        self.assertIn("mirror-like-1", [row["eventId"] for row in posted])
        self.assertTrue(board["mirror"]["enabled"])
        self.assertGreaterEqual(board["mirror"]["sent"], 1)

    def test_dashboard_reports_mirror_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            host = HostState(EventQueue(), self.mirror_settings(Path(folder)))
            board = host.dashboard()
        self.assertFalse(board["mirror"]["enabled"])
        self.assertEqual(board["mirror"]["sent"], 0)

    def test_health_reports_version(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(self.mirror_settings(Path(folder)), EventQueue())
            health = TestClient(app).get("/v1/health").json()
        self.assertTrue(health["ok"])
        self.assertEqual(health["version"], "1.4.0")

    def test_stress_join_is_mirrored(self) -> None:
        posted: list[dict] = []

        def post(url, key, payload, timeout):
            posted.extend(payload["events"])
            return 200, json.dumps({"results": [{"status": "created"} for _ in payload["events"]]})

        mirror = UpstreamMirror("https://python-bridge.invalid", INGEST, post=post)
        try:
            with tempfile.TemporaryDirectory() as folder:
                host = HostState(EventQueue(), self.mirror_settings(Path(folder)), mirror)
                host.start_stress("join10", 0.0)
                deadline = time.time() + 10
                while time.time() < deadline and host.stress["running"]:
                    time.sleep(0.05)
                self.assertTrue(mirror.wait_idle(5.0))
                sent = host.stress["sent"]
                queued = host.queue.status()["depth"]
        finally:
            mirror.close()
        self.assertEqual(sent, 10)
        self.assertEqual(queued, 10)
        self.assertGreaterEqual(len(posted), 10)







class SimBridge:
    """In-process stand-in for the deployed bridge: ingest plus the ack history."""

    def __init__(self, queue: EventQueue) -> None:
        self.queue = queue
        self.down = False
        self.posts = 0
        self.posted_ids: list[str] = []

    def post(self, url: str, key: str, payload: dict, timeout: float) -> tuple[int, str]:
        if self.down:
            raise OSError("sim offline")
        self.posts += 1
        results = []
        for event in payload["events"]:
            status, sequence = self.queue.publish(event, rate_limit=False)
            results.append({"status": status, "sequence": sequence})
            self.posted_ids.append(event["eventId"])
        state = self.queue.status()
        body = {
            "results": results,
            "sessionId": state["sessionId"],
            "resetEpoch": state["resetEpoch"],
            "depth": state["depth"],
        }
        return 200, json.dumps(body)

    def fetch(self, url: str, key: str, after: int, timeout: float) -> tuple[int, str]:
        if self.down:
            raise OSError("sim offline")
        journal = self.queue.journal
        if journal is None:
            state = self.queue.status()
            body = {
                "after": after,
                "next": after,
                "ids": [],
                "sessionId": state["sessionId"],
                "resetEpoch": state["resetEpoch"],
            }
        else:
            body = journal.acks_after(after, 200)
        return 200, json.dumps(body)

    def roblox(self, limit: int = 20) -> list[int]:
        """One poll cycle: pull a batch, process it, ack it."""
        status, rows = self.queue.pull(limit)
        sequences = [row["sequence"] for row in rows]
        if sequences:
            self.queue.ack(sequences)
        return sequences


class DurabilityTests(unittest.TestCase):
    """Phase 9H: a restart, a redeploy or a 30-120 s outage must not lose or repeat events."""

    def sample(self, index: int) -> dict:
        return {
            "eventId": "durable-%03d" % index,
            "type": "like",
            "viewerId": "dv%03d" % index,
            "nickname": "dv%03d" % index,
            "timestamp": 2000 + index,
            "payload": {"count": 1},
        }

    def wait_for(self, check, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if check():
                return True
            time.sleep(0.02)
        return check()

    def test_journal_keeps_queue_and_session_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue.sqlite3"
            first = EventQueue(journal_path=path)
            created = []
            for event in join_batch():
                status, sequence = first.publish(event, rate_limit=False)
                self.assertEqual(status, "created")
                created.append(sequence)
            session = first.status()["sessionId"]
            self.assertTrue(first.durable)
            first.journal.close()

            second = EventQueue(journal_path=path)
            state = second.status()
            self.assertEqual(state["sessionId"], session)
            self.assertEqual(state["depth"], 15)
            self.assertEqual(state["recovered"], 15)
            self.assertEqual([row["sequence"] for row in second.pull(20)[1]], created)
            self.assertEqual(second.publish(join_batch()[0], rate_limit=False)[0], "duplicate")
            fresh = {
                "eventId": "after-restart-1",
                "type": "comment",
                "viewerId": "v-99",
                "nickname": "Khách 99",
                "timestamp": 1_700_000_500,
                "payload": {"text": "/câu"},
            }
            status, sequence = second.publish(fresh, rate_limit=False)
            self.assertEqual(status, "created")
            self.assertEqual(sequence, 16)
            self.assertEqual(second.ack(created)[1], 15)
            rows = second.pull(20)[1]
            self.assertEqual([row["sequence"] for row in rows], [16])
            self.assertEqual(second.status()["acked"], 15)
            second.journal.close()

    def test_ack_history_and_reset_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue.sqlite3"
            queue = EventQueue(journal_path=path)
            sequences = [queue.publish(event, rate_limit=False)[1] for event in interaction_batch()]
            queue.ack(sequences[:2])
            answer = queue.journal.acks_after(0, 200)
            self.assertEqual(len(answer["ids"]), 2)
            self.assertEqual(answer["next"], 2)
            self.assertEqual(answer["cursor"], 2)
            self.assertEqual(queue.journal.acks_after(2, 200)["ids"], [])
            session = queue.reset()
            state = queue.status()
            self.assertEqual(state["resetEpoch"], 1)
            self.assertEqual(state["depth"], 0)
            self.assertEqual(queue.journal.acks_after(0, 200)["ids"], [])
            queue.journal.close()

            reopened = EventQueue(journal_path=path)
            self.assertEqual(reopened.status()["sessionId"], session)
            self.assertEqual(reopened.status()["resetEpoch"], 1)
            reopened.journal.close()

    def test_journal_files_hold_no_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            queue_path = Path(folder) / "queue.sqlite3"
            outbox_path = Path(folder) / "outbox.sqlite3"
            queue = EventQueue(journal_path=queue_path)
            for event in interaction_batch():
                queue.publish(event, rate_limit=False)
            sim = SimBridge(queue)
            mirror = UpstreamMirror(
                "https://python-bridge.invalid",
                INGEST,
                post=sim.post,
                fetch=sim.fetch,
                outbox_path=outbox_path,
                resend_after=60.0,
            )
            try:
                mirror.submit(self.sample(1))
                self.assertTrue(mirror.wait_idle(5.0))
            finally:
                mirror.close()
            queue.journal.close()
            for path in (queue_path, outbox_path):
                blob = path.read_bytes()
                self.assertNotIn(INGEST.encode("utf-8"), blob)
                self.assertNotIn(READ.encode("utf-8"), blob)

    def test_durable_mirror_survives_queue_restart(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            queue_path = Path(folder) / "queue.sqlite3"
            outbox_path = Path(folder) / "outbox.sqlite3"
            queue = EventQueue(journal_path=queue_path)
            sim = SimBridge(queue)
            mirror = UpstreamMirror(
                "https://python-bridge.invalid",
                INGEST,
                post=sim.post,
                fetch=sim.fetch,
                outbox_path=outbox_path,
                resend_after=60.0,
            )
            try:
                for index in range(5):
                    mirror.submit(self.sample(index))
                self.assertTrue(mirror.wait_idle(5.0))
                self.assertEqual(queue.status()["depth"], 5)
                session = queue.status()["sessionId"]
                mirror.close()
                queue.journal.close()

                queue = EventQueue(journal_path=queue_path)
                sim = SimBridge(queue)
                mirror = UpstreamMirror(
                    "https://python-bridge.invalid",
                    INGEST,
                    post=sim.post,
                    fetch=sim.fetch,
                    outbox_path=outbox_path,
                    resend_after=60.0,
                )
                self.assertEqual(queue.status()["sessionId"], session)
                self.assertEqual(queue.status()["depth"], 5)
                self.assertEqual(len(sim.roblox()), 5)
                self.assertTrue(self.wait_for(lambda: mirror.status()["queued"] == 0, 8.0))
                status = mirror.status()
                self.assertEqual(status["acked"], 5)
                self.assertEqual(status["failed"], 0)
                self.assertEqual(status["expired"], 0)
                self.assertIsNotNone(status["ackLatencyMs"])
            finally:
                mirror.close()
                journal = queue.journal
                if journal is not None:
                    journal.close()


    def test_durable_mirror_survives_network_loss(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            queue_path = Path(folder) / "queue.sqlite3"
            outbox_path = Path(folder) / "outbox.sqlite3"
            queue = EventQueue(journal_path=queue_path)
            sim = SimBridge(queue)
            sim.down = True
            mirror = UpstreamMirror(
                "https://python-bridge.invalid",
                INGEST,
                post=sim.post,
                fetch=sim.fetch,
                outbox_path=outbox_path,
                resend_after=60.0,
            )
            try:
                for index in range(40):
                    mirror.submit(self.sample(index))
                self.assertTrue(self.wait_for(lambda: mirror.status()["deferred"] > 0, 10.0))
                offline = mirror.status()
                self.assertEqual(offline["queued"], 40)
                self.assertEqual(offline["sent"], 0)
                self.assertGreaterEqual(offline["failures"], 1)
                self.assertTrue(offline["lastError"])

                sim.down = False
                self.assertTrue(mirror.wait_idle(20.0))
                self.assertEqual(queue.status()["depth"], 40)
                pending = 40
                while pending > 0:
                    pulled = sim.roblox(20)
                    if not pulled:
                        break
                    pending -= len(pulled)
                self.assertTrue(self.wait_for(lambda: mirror.status()["queued"] == 0, 10.0))
                status = mirror.status()
                self.assertEqual(status["acked"], 40)
                self.assertEqual(status["failed"], 0)
                self.assertEqual(status["expired"], 0)
                self.assertEqual(queue.status()["depth"], 0)
            finally:
                mirror.close()
                journal = queue.journal
                if journal is not None:
                    journal.close()

    def test_durable_mirror_replays_after_session_change(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            queue_path = Path(folder) / "queue.sqlite3"
            outbox_path = Path(folder) / "outbox.sqlite3"
            queue = EventQueue(journal_path=queue_path)
            sim = SimBridge(queue)
            mirror = UpstreamMirror(
                "https://python-bridge.invalid",
                INGEST,
                post=sim.post,
                fetch=sim.fetch,
                outbox_path=outbox_path,
                resend_after=60.0,
            )
            try:
                for index in range(3):
                    mirror.submit(self.sample(index))
                self.assertTrue(mirror.wait_idle(5.0))
                posts_before = sim.posts
                queue.reset()  # a redeploy / explicit reset: memory and dedup are new
                self.assertTrue(self.wait_for(lambda: mirror.status()["sessionChanges"] > 0, 8.0))
                self.assertTrue(self.wait_for(lambda: sim.posts > posts_before, 8.0))
                self.assertGreaterEqual(mirror.status()["replayed"], 1)
                self.assertEqual(queue.status()["depth"], 3)
                pending = 3
                while pending > 0:
                    pulled = sim.roblox(20)
                    if not pulled:
                        break
                    pending -= len(pulled)
                self.assertTrue(self.wait_for(lambda: mirror.status()["queued"] == 0, 10.0))
                self.assertEqual(mirror.status()["acked"], 3)
            finally:
                mirror.close()
                journal = queue.journal
                if journal is not None:
                    journal.close()

    def test_api_reports_durability(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue.sqlite3"
            queue = EventQueue(journal_path=path)
            client = TestClient(create_app(settings(path), queue))
            health = client.get("/v1/health").json()
            self.assertTrue(health["durable"])
            self.assertEqual(health["resetEpoch"], 0)
            self.assertEqual(health["ackCursor"], 0)
            denied = client.get("/v1/acks", headers={"X-Bridge-Key": READ})
            self.assertEqual(denied.status_code, 401)
            event = join_batch()[0]
            client.post("/v1/events", headers={"X-Bridge-Key": INGEST}, json=event)
            pulled = client.get("/v1/events", headers={"x-api-key": READ}).json()
            self.assertEqual(pulled["resetEpoch"], 0)
            sequences = [row["sequence"] for row in pulled["events"]]
            client.post("/v1/events/ack", headers={"x-api-key": READ}, json={"sequences": sequences})
            acks = client.get("/v1/acks?after=0", headers={"X-Bridge-Key": INGEST}).json()
            self.assertEqual(acks["ids"], [event["eventId"]])
            self.assertEqual(acks["next"], len(sequences))
            self.assertEqual(acks["cursor"], len(sequences))
            self.assertEqual(acks["sessionId"], health["sessionId"])
            queue.journal.close()

    def test_journal_paths_come_from_env(self) -> None:
        env = {
            "BRIDGE_MODE": "mock",
            "BRIDGE_INGEST_KEY": INGEST,
            "BRIDGE_READ_KEY": READ,
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_settings(Path("does-not-exist.env"))
        self.assertTrue(cfg.journal_path.endswith("queue-journal.sqlite3"))
        self.assertTrue(cfg.outbox_path.endswith("mirror-outbox.sqlite3"))
        self.assertEqual(cfg.outbox_ttl, 1800.0)


class FakeLive:
    """Minimal stand-in for TikTokSource; records the switch without a network call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.state = {
            "username": "old_host",
            "connected": False,
            "lastError": "",
            "switches": 0,
            "hasSession": False,
            "updatedAt": 0.0,
        }

    def status(self) -> dict:
        return dict(self.state)

    def apply(self, username: str, session_id: str = "", target_idc: str = "") -> dict:
        if not username:
            raise ValueError("invalid_username")
        self.calls.append((username, session_id, target_idc))
        self.state = {
            **self.state,
            "username": username,
            "switches": self.state["switches"] + 1,
            "hasSession": bool(session_id),
        }
        return dict(self.state)


class LiveSwitchTests(unittest.TestCase):
    def test_switch_endpoint_is_authenticated_and_live_only(self) -> None:
        auth = {"X-Bridge-Key": INGEST}
        with tempfile.TemporaryDirectory() as folder:
            cfg = replace(settings(Path(folder) / "queue.json"), mode="live", tiktok_username="old_host")
            fake = FakeLive()
            live = TestClient(create_app(cfg, EventQueue(), live=fake))
            self.assertEqual(live.get("/v1/tiktok").status_code, 401)
            switched = live.post(
                "/v1/tiktok/switch",
                headers=auth,
                json={"username": "@New_Host", "sessionId": "sid-value", "targetIdc": "useast5"},
            )
            self.assertEqual(switched.status_code, 200)
            self.assertEqual(fake.calls, [("new_host", "sid-value", "useast5")])
            self.assertNotIn("sid-value", switched.text)
            self.assertNotIn("session", switched.json())
            self.assertEqual(
                live.post("/v1/tiktok/switch", headers=auth, json={"username": "a b"}).status_code, 422
            )
            self.assertEqual(
                live.post(
                    "/v1/tiktok/switch", headers=auth, json={"username": "new_host", "sessionId": "sid"}
                ).status_code,
                422,
            )
            status = live.get("/v1/tiktok", headers=auth).json()
            self.assertTrue(status["live"])
            self.assertEqual(status["username"], "new_host")
            self.assertTrue(status["hasSession"])

            mock = TestClient(create_app(settings(Path(folder) / "other.json"), EventQueue()))
            self.assertEqual(
                mock.post("/v1/tiktok/switch", headers=auth, json={"username": "new_host"}).status_code, 409
            )
            mock_status = mock.get("/v1/tiktok", headers=auth).json()
            self.assertFalse(mock_status["live"])
            self.assertEqual(mock_status["mode"], "mock")

    def test_host_proxy_validates_and_needs_a_mirror(self) -> None:
        page = Path(__file__).resolve().parents[1] / "host-controller"
        with tempfile.TemporaryDirectory() as folder:
            cfg = replace(
                settings(Path(folder) / "queue.json"),
                live_settings_path=str(Path(folder) / "live-settings.json"),
            )
            app = create_app(cfg, EventQueue(), host_dir=page)
            local = TestClient(app, client=("127.0.0.1", 50000))
            headers = {"Host": "127.0.0.1:8787", "Origin": "http://127.0.0.1:8787"}
            token = local.get("/host/session", headers=headers).json()["csrf"]
            auth = {**headers, "X-CSRF-Token": token}
            invalid = local.post(
                "/host/action",
                headers=auth,
                json={"action": "live_switch_tiktok", "tiktokUsername": "a b", "sessionId": "", "targetIdc": ""},
            ).json()
            self.assertFalse(invalid["ok"])
            self.assertEqual(invalid["error"], "invalid")
            missing_idc = local.post(
                "/host/action",
                headers=auth,
                json={"action": "live_switch_tiktok", "tiktokUsername": "studio_host", "sessionId": "sid", "targetIdc": ""},
            ).json()
            self.assertFalse(missing_idc["ok"])
            self.assertEqual(missing_idc["error"], "invalid")
            no_mirror = local.post(
                "/host/action",
                headers=auth,
                json={"action": "live_switch_tiktok", "tiktokUsername": "studio_host", "sessionId": "", "targetIdc": ""},
            ).json()
            self.assertFalse(no_mirror["ok"])
            self.assertEqual(no_mirror["error"], "remote")
            self.assertIn("BRIDGE_MIRROR_URL", no_mirror["detail"])
            status = local.post("/host/action", headers=auth, json={"action": "live_tiktok_status"}).json()
            self.assertFalse(status["ok"])
            self.assertEqual(status["error"], "remote")



def logging_record(message: str):
    import logging

    return logging.LogRecord("test", logging.INFO, __file__, 1, message, (), None)


if __name__ == "__main__":
    unittest.main()
