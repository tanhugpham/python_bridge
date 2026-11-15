"""Local host-controller state. Reuses EventQueue. Does not open TikTok."""

from __future__ import annotations

import ctypes
import secrets
import threading
import time
from collections import deque
from typing import Any
from urllib.parse import urlsplit

from fishinglive_bridge.config import Settings
from fishinglive_bridge.queue_store import EventQueue
from fishinglive_bridge.schema import parse_event

JOIN_TEXT = {"cau", "câu", "/cau", "/câu"}
PULL_TEXT = {"/giat", "/giật"}
VITRI_TEXT = {"/vitri", "/vịtrí"}
BAITS = (
    {"tier": 1, "id": "BAIT_TIER_1", "name": "Mồi Giun Đỏ", "coins": 10, "color": "#C4302A", "effect": "spark_small"},
    {"tier": 2, "id": "BAIT_TIER_2", "name": "Mồi Tôm Bạc", "coins": 30, "color": "#C6CED6", "effect": "bubbles"},
    {"tier": 3, "id": "BAIT_TIER_3", "name": "Mồi Cá Vàng", "coins": 50, "color": "#E8BA40", "effect": "glow"},
    {"tier": 4, "id": "BAIT_TIER_4", "name": "Mồi Ngọc Biển", "coins": 100, "color": "#24B4A8", "effect": "water_glow"},
    {"tier": 5, "id": "BAIT_TIER_5", "name": "Mồi Rồng Thần", "coins": 200, "color": "#9A4ED6", "effect": "legendary"},
)
BAIT_BY_ID = {row["id"]: row for row in BAITS}
FORCE_KINDS = {
    "ForceFishSuccess",
    "ForceFishEscape",
    "ForceFishRarity",
    "ForceFishWeight",
    "ForceOwnershipWinner",
    "ForceStruggleSuccess",
    "ForceStruggleFailure",
}
CAMERA_SHOTS = {
    "DIORAMA",
    "FISHING_SHOWCASE",
    "FISH_RESULT",
    "FISH_ESCAPE",
    "GIFT_CAMERA_CENTER",
    "VITRI_FOCUS",
    "TOP10_LEADERBOARD",
}
GIFT_PURPOSES = {"steal", "camera", "bait", "normal"}
LOG_LIMIT = 200
VIEWER_PAGE = 50
ROBLOX_PULL_WINDOW = 12.0
STRESS_KINDS = {
    "join10",
    "join100",
    "join500",
    "likes100",
    "giat100",
    "gifts100",
    "duplicate",
    "overload",
    "backend_restart",
    "roblox_disconnected",
    "roblox_reconnect",
}


def _now() -> float:
    return time.time()


def clip(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else ""
    if len(text.encode("utf-8")) <= limit:
        return text
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def valid_user_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if len(token) < 2 or len(token) > 64:
        return None
    if not all(char.isalnum() or char in "_-" for char in token):
        return None
    return token


def valid_avatar(value: Any) -> str | None:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if len(url) > 200 or not (url.startswith("https://") or url.startswith("http://")):
        return None
    if any(ord(char) < 32 or char in "<>\"'" for char in url):
        return None
    return url


def valid_username(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if len(name) < 2 or len(name) > 32:
        return None
    if any(ord(char) < 32 or char in " \t/\\" for char in name):
        return None
    return name


def normalize_comment(text: str) -> str:
    return " ".join(text.strip().lower().split())


def process_sample(previous: dict[str, float] | None) -> dict[str, Any]:
    rss = _rss_bytes()
    cpu_seconds = _cpu_seconds()
    now = time.monotonic()
    percent = None
    if previous and cpu_seconds is not None and previous.get("cpu") is not None:
        wall = now - previous["at"]
        if wall >= 0.2:
            percent = max(0.0, (cpu_seconds - previous["cpu"]) / wall * 100.0)
            percent = round(min(percent, 100.0), 1)
    return {
        "rssBytes": rss,
        "cpuPercent": percent,
        "cpuNote": None if percent is not None else "Đang lấy mẫu CPU",
        "_cpu": cpu_seconds,
        "_at": now,
    }


def _rss_bytes() -> int | None:
    if hasattr(ctypes, "windll"):
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_uint32),
                ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32")
        kernel.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_uint32]
        kernel.K32GetProcessMemoryInfo.restype = ctypes.c_int
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        ok = kernel.K32GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        if ok:
            return int(counters.WorkingSetSize)
        return None
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes. macOS reports bytes.
        if usage > 10_000_000:
            return int(usage)
        return int(usage * 1024)
    except Exception:
        return None


def _cpu_seconds() -> float | None:
    if hasattr(ctypes, "windll"):
        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

        created, exited, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
        win = ctypes.WinDLL("kernel32")
        win.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.POINTER(FileTime), ctypes.POINTER(FileTime), ctypes.POINTER(FileTime), ctypes.POINTER(FileTime)]
        win.GetProcessTimes.restype = ctypes.c_int
        ok = win.GetProcessTimes(
            win.GetCurrentProcess(),
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None

        def seconds(item: FileTime) -> float:
            return ((item.high << 32) + item.low) / 10_000_000

        return seconds(kernel) + seconds(user)
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        return float(usage.ru_utime + usage.ru_stime)
    except Exception:
        return None


class HostState:
    def __init__(self, queue: EventQueue, settings: Settings) -> None:
        self.queue = queue
        self.settings = settings
        self.lock = threading.Lock()
        self.viewers: dict[str, dict[str, Any]] = {}
        self.by_name: dict[str, str] = {}
        self.name_serial = 0
        self.log: deque[dict[str, Any]] = deque(maxlen=LOG_LIMIT)
        self.by_sequence: dict[int, dict[str, Any]] = {}
        self.tokens: dict[str, float] = {}
        self.sample: dict[str, float] | None = None
        self.stress_stop = threading.Event()
        self.stress: dict[str, Any] = {
            "running": False,
            "kind": "",
            "sent": 0,
            "note": "",
        }
        self.commands: deque[dict[str, Any]] = deque(maxlen=40)
        self.session_on = True

    def issue_token(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = _now()
            expired = [key for key, until in self.tokens.items() if until <= now]
            for key in expired:
                self.tokens.pop(key, None)
            if len(self.tokens) > 8:
                oldest = sorted(self.tokens, key=lambda key: self.tokens[key])[0]
                self.tokens.pop(oldest, None)
            self.tokens[token] = now + 8 * 3600
        return token

    def token_ok(self, token: str) -> bool:
        if not token or len(token) > 128:
            return False
        with self.lock:
            until = self.tokens.get(token, 0)
            if until <= _now():
                self.tokens.pop(token, None)
                return False
            return True

    def roblox_label(self, state: dict[str, Any] | None = None) -> str:
        state = state if state is not None else self.queue.status()
        last_pull = float(state.get("lastPullAt") or 0)
        if last_pull <= 0 or (_now() - last_pull) > ROBLOX_PULL_WINDOW:
            return "ROBLOX DISCONNECTED"
        return "ROBLOX CONNECTED"

    def dashboard(self) -> dict[str, Any]:
        state = self.queue.status()
        sample = process_sample(self.sample)
        self.sample = {"cpu": sample.pop("_cpu"), "at": sample.pop("_at")}
        with self.lock:
            viewers = list(self.viewers.values())
            stress = dict(self.stress)
            session_on = self.session_on
        likes = sum(int(row["likes"]) for row in viewers)
        gifts = sum(int(row["gifts"]) for row in viewers)
        label = self.roblox_label(state)
        return {
            "bridge": {
                "ok": True,
                "mode": self.settings.mode,
                "sessionId": state["sessionId"],
                "depth": state["depth"],
                "capacity": state["capacity"],
                "accepted": state["accepted"],
                "dropped": state["dropped"],
                "duplicates": state["duplicates"],
                "coalesced": state["coalesced"],
            },
            "roblox": {
                "label": label,
                "connected": label == "ROBLOX CONNECTED",
                "lastPullAt": state["lastPullAt"],
                "lastAckAt": state["lastAckAt"],
                "pulls": state["pulls"],
                "acks": state["acks"],
                "telemetry": None,
                "telemetryNote": "Chưa kết nối telemetry Roblox",
            },
            "mockMode": not self.settings.live,
            "viewers": len(viewers),
            "activeNpc": None,
            "totalLikes": likes,
            "totalGifts": gifts,
            "totalFish": None,
            "totalMisses": None,
            "projectionNote": "Like, quà và viewer là sự kiện bridge đã nhận. Cá, NPC và điểm chưa có nguồn Roblox.",
            "process": sample,
            "batchLatencyMs": state["batchLatencyMs"],
            "stress": stress,
            "sessionOn": session_on,
            "httpsTunnel": {
                "connected": False,
                "label": "KHÔNG CHẠY",
                "evidence": "not_started",
            },
        }

    def viewer_page(self, query: str, offset: int) -> dict[str, Any]:
        needle = query.strip().lower()
        with self.lock:
            rows = list(self.viewers.values())
        if needle:
            rows = [row for row in rows if needle in row["username"].lower() or needle in row["userId"].lower()]
        rows.sort(key=lambda row: row["username"])
        offset = max(0, offset)
        page = rows[offset:offset + VIEWER_PAGE]
        public = []
        for row in page:
            public.append({
                "username": row["username"],
                "userId": row["userId"],
                "likes": row["likes"],
                "fishingUnlocked": row["fishingUnlocked"],
                "fishingEnabled": row["fishingEnabled"],
                "totalScore": None,
                "biggestFish": None,
                "lastActivity": row["lastActivity"],
                "gifts": row["gifts"],
                "baitTier": row["baitTier"],
                "avatarUrl": row.get("avatarUrl") or "",
                "mockPoints": row.get("mockPoints", 0),
                "source": "bridge",
            })
        return {"total": len(rows), "offset": offset, "limit": VIEWER_PAGE, "viewers": public}

    def baits(self) -> dict[str, Any]:
        with self.lock:
            using: dict[int, list[str]] = {}
            for row in self.viewers.values():
                tier = row["baitTier"]
                if isinstance(tier, int):
                    using.setdefault(tier, []).append(row["username"])
        catalog = []
        for bait in BAITS:
            names = using.get(bait["tier"], [])
            catalog.append({
                **bait,
                "viewers": names[:20],
                "viewerCount": len(names),
            })
        return {
            "approvedForLive": False,
            "source": "place-default-mock",
            "note": "Cấu hình mock đã có trong place. Controller không ghi đè. Xác suất cá không đổi.",
            "tiers": catalog,
        }

    def leaderboard(self) -> dict[str, Any]:
        return {
            "source": "none",
            "rows": [],
            "note": "Chưa có endpoint đọc TOP 10 từ Roblox. Đề xuất Roblox gửi snapshot khi poll, bridge giữ bản cuối. Hiện không bịa điểm.",
        }

    def struggle(self) -> dict[str, Any]:
        return {
            "source": "none",
            "currentFisher": None,
            "currentChallenger": None,
            "countdown": None,
            "strength": None,
            "fishRarity": None,
            "showcaseState": None,
            "note": "Chưa có API trạng thái giật cá từ Roblox. Không hiển thị số giả.",
        }

    def results(self) -> dict[str, Any]:
        return {
            "source": "none",
            "queue": [],
            "wins": None,
            "misses": None,
            "note": "Chưa có hàng kết quả từ Roblox.",
        }

    def events(self, limit: int = 80) -> dict[str, Any]:
        limit = max(1, min(limit, 100))
        with self.lock:
            rows = list(self.log)[:limit]
        return {"events": rows}

    def metrics(self) -> dict[str, Any]:
        board = self.dashboard()
        return {
            "process": board["process"],
            "queue": {
                "depth": board["bridge"]["depth"],
                "capacity": board["bridge"]["capacity"],
                "accepted": board["bridge"]["accepted"],
                "dropped": board["bridge"]["dropped"],
                "duplicates": board["bridge"]["duplicates"],
                "batchLatencyMs": board["batchLatencyMs"],
            },
            "roblox": {
                "fps": None,
                "memory": None,
                "instances": None,
                "humanoids": None,
                "activeRigs": None,
                "cameraQueue": None,
                "note": "Chưa kết nối telemetry Roblox",
            },
        }

    def reset_mock(self) -> dict[str, Any]:
        if self.settings.live:
            return self._blocked()
        self.stop_stress()
        session = self.queue.reset()
        with self.lock:
            self.viewers.clear()
            self.by_name.clear()
            self.log.clear()
            self.by_sequence.clear()
            self.commands.clear()
        return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", "Đã xóa phiên mock trên bridge. Roblox không bị reset từ trang này.", extra={"sessionId": session})

    def act(self, body: dict[str, Any], *, pace: float | None = None) -> dict[str, Any]:
        action = body.get("action")
        if action == "start_session":
            return self.start_session()
        if action == "stop_session":
            return self.stop_session()
        if action == "clear_log":
            return self.clear_log()
        if action == "reset_session":
            return self.reset_mock()
        if action == "stress_stop":
            return self.stop_stress()
        if not self._session_open() and action not in {
            "refresh_leaderboard",
            "reset_leaderboard",
            "preview_bait",
            "reset_viewer",
        }:
            return self._fail("paused", "Phiên test đã dừng. Trang không bật HTTP Roblox.")
        if action == "reset_viewer":
            return self.reset_viewer(body.get("username"))
        if action == "basic_test":
            return self.basic_test()
        if action == "test_event":
            return self.test_event(body.get("username"))
        if action == "stress":
            kind = body.get("kind")
            if kind not in STRESS_KINDS:
                return self._fail("invalid", "Bài test không hợp lệ.")
            return self.start_stress(str(kind), pace if pace is not None else 0.025)
        if self.settings.live and action not in {"refresh"}:
            return self._blocked()
        if action == "add_viewer":
            return self.add_viewer(
                body.get("username"),
                allow_duplicate=body.get("allowDuplicate") is True,
                user_id=body.get("userId"),
                avatar_url=body.get("avatarUrl"),
                likes=body.get("likes", 0),
                points=body.get("points", 0),
            )
        if action == "remove_viewer":
            return self.remove_viewer(body.get("username"))
        if action == "add_viewers":
            return self.add_viewers(body.get("count"))
        if action == "like":
            return self.send_like(body.get("username"), body.get("count"), body.get("eventId"))
        if action == "comment":
            return self.send_comment(body.get("username"), body.get("text"), body.get("eventId"))
        if action == "gift":
            return self.send_gift(body)
        if action == "register_fishing":
            return self.send_comment(body.get("username"), "câu", body.get("eventId"))
        if action == "pull":
            count = body.get("count", 1)
            return self.send_pulls(body.get("username"), count)
        if action == "force":
            return self.force_command(body.get("kind"), body.get("username"))
        if action == "camera":
            return self.camera_command(body.get("shot"), body.get("username"))
        if action == "support_comment":
            return self.support_comment(body.get("username"))
        if action == "gift_steal":
            return self.gift_steal(body.get("username"))
        if action == "preview_bait":
            return self.preview_bait(body.get("tier"), body.get("username"))
        if action == "refresh_leaderboard":
            return {**self.leaderboard(), "bridgeStatus": "ACCEPTED_BY_BRIDGE", "robloxStatus": "NOT_DELIVERED"}
        if action == "preview_leaderboard_camera":
            return self.camera_command("TOP10_LEADERBOARD", body.get("username"))
        if action == "reset_leaderboard":
            return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", "Không có điểm mock để xóa. Không tạo hệ điểm thứ hai.")
        return self._fail("invalid", "Hành động không hợp lệ.")

    def start_session(self) -> dict[str, Any]:
        with self.lock:
            self.session_on = True
        return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", "Đã bắt đầu phiên giả lập. Không bật HTTP Roblox.")

    def stop_session(self) -> dict[str, Any]:
        self.stop_stress()
        with self.lock:
            self.session_on = False
        return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", "Đã dừng phiên giả lập. Hàng đợi giữ nguyên. Không đổi cấu hình Roblox.")

    def clear_log(self) -> dict[str, Any]:
        with self.lock:
            self.log.clear()
        return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", "Đã xóa nhật ký hiển thị. Hàng đợi không đổi.")

    def _session_open(self) -> bool:
        with self.lock:
            return self.session_on

    def add_viewer(
        self,
        username: Any,
        allow_duplicate: bool = False,
        user_id: Any = None,
        avatar_url: Any = None,
        likes: Any = 0,
        points: Any = 0,
    ) -> dict[str, Any]:
        if username in (None, ""):
            name = self._alloc_name()
        else:
            name = valid_username(username)
            if name is None:
                return self._fail("invalid", "Username không hợp lệ.")
        if user_id in (None, ""):
            chosen_id = "mu" + secrets.token_hex(5)
        else:
            chosen_id = valid_user_id(user_id)
            if chosen_id is None:
                return self._fail("invalid", "User ID không hợp lệ.")
        avatar = valid_avatar(avatar_url)
        if avatar is None:
            return self._fail("invalid", "Avatar URL không hợp lệ.")
        if isinstance(points, bool) or not isinstance(points, int) or points < 0 or points > 1_000_000:
            return self._fail("invalid", "Điểm mock không hợp lệ.")
        if isinstance(likes, bool) or not isinstance(likes, int) or likes < 0 or likes > 10000:
            return self._fail("invalid", "Số like phải từ 0 đến 10000.")
        with self.lock:
            if name in self.by_name and not allow_duplicate:
                return self._fail("duplicate_name", "Username đã có trong phiên mock.")
            if chosen_id in self.viewers:
                return self._fail("duplicate_name", "User ID đã có trong phiên mock.")
            row = self._blank(name, chosen_id)
            row["avatarUrl"] = avatar
            row["mockPoints"] = points
            self.viewers[chosen_id] = row
            self.by_name[name] = chosen_id
        event = self._event("comment", chosen_id, name, {"text": "join"}, None)
        result = self._publish(event)
        if likes > 0 and result.get("bridgeStatus") == "QUEUED":
            liked = self.send_like(name, likes, None)
            liked["detail"] = "Đã thêm viewer. " + str(liked.get("detail") or "")
            return liked
        result["viewer"] = self._public_viewer(chosen_id)
        result["detail"] = "Đã thêm viewer và xếp comment join vào hàng đợi." if result["bridgeStatus"] == "QUEUED" else result["detail"]
        return result

    def add_viewers(self, count: Any) -> dict[str, Any]:
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 500:
            return self._fail("invalid", "Số viewer phải từ 1 đến 500.")
        created = 0
        last = None
        for _ in range(count):
            if self.queue.status()["depth"] >= self.queue.capacity:
                break
            last = self.add_viewer(None)
            if last.get("bridgeStatus") == "QUEUED":
                created += 1
            else:
                break
        return self._result("QUEUED" if created else "FAILED", "NOT_DELIVERED", None, "", f"Đã xếp {created} viewer.", extra={"created": created, "last": last})

    def remove_viewer(self, username: Any) -> dict[str, Any]:
        name = valid_username(username)
        if name is None:
            return self._fail("invalid", "Username không hợp lệ.")
        with self.lock:
            user_id = self.by_name.pop(name, None)
            if user_id is None:
                return self._fail("missing", "Không thấy viewer.")
            self.viewers.pop(user_id, None)
        self._command_log("remove_viewer", name, "Đã gỡ khỏi phiên mock. Schema không có sự kiện leave nên Roblox không nhận lệnh rời.")
        return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", "Đã gỡ khỏi phiên mock của bridge. Roblox chưa nhận lệnh rời.")

    def reset_viewer(self, username: Any) -> dict[str, Any]:
        name = valid_username(username)
        if name is None:
            return self._fail("invalid", "Username không hợp lệ.")
        with self.lock:
            user_id = self.by_name.get(name)
            row = self.viewers.get(user_id) if user_id else None
            if row is None:
                return self._fail("missing", "Không thấy viewer.")
            row["likes"] = 0
            row["fishingUnlocked"] = False
            row["fishingEnabled"] = False
            row["pulls"] = 0
            row["baitTier"] = None
            row["mockPoints"] = 0
            row["lastActivity"] = _now()
        self._command_log("reset_viewer", name, "Đặt lại like và mở khóa trên phép chiếu bridge. Không gửi sự kiện.")
        return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", "Đã đặt lại viewer trên bridge. Không bịa điểm Roblox.")

    def test_event(self, username: Any) -> dict[str, Any]:
        result = self.send_comment(username, "xin chao", None)
        if result.get("bridgeStatus") == "QUEUED":
            result["detail"] = "Đã gửi một comment thử. Không phải lệnh câu. Roblox chưa ACK."
        return result

    def basic_test(self) -> dict[str, Any]:
        if self.settings.live:
            return self._blocked()
        if self.queue.status()["depth"] > 1500:
            return self._fail("busy", "Hàng đợi gần đầy. Reset phiên trước.")
        name = "t" + secrets.token_hex(3)
        scenarios: list[dict[str, Any]] = []
        created = self.add_viewer(name)
        liked = self.send_like(name, 500, None)
        viewer = liked.get("viewer") or {}
        scenarios.append({
            "id": 1,
            "ok": created.get("bridgeStatus") == "QUEUED" and viewer.get("fishingUnlocked") is True and viewer.get("likes") == 500,
            "scope": "bridge",
        })
        joined = self.send_comment(name, "/cau", None)
        scenarios.append({
            "id": 2,
            "ok": joined.get("bridgeStatus") == "QUEUED" and (joined.get("viewer") or {}).get("fishingEnabled") is True,
            "scope": "bridge",
        })
        camera = self.camera_command("FISHING_SHOWCASE", name)
        scenarios.append({"id": 3, "ok": camera.get("appliedOnRoblox") is False, "scope": "roblox_not_tested"})
        pull = self.send_comment(name, "/giat", None)
        scenarios.append({"id": 4, "ok": pull.get("bridgeStatus") == "QUEUED", "scope": "bridge"})
        steal = self.send_gift({
            "username": name,
            "purpose": "steal",
            "giftId": "GIFT_STEAL",
            "giftName": "Rose",
            "coinValue": 20,
            "repeatCount": 1,
        })
        scenarios.append({"id": 5, "ok": steal.get("bridgeStatus") == "QUEUED", "scope": "bridge"})
        gift_camera = self.send_gift({
            "username": name,
            "purpose": "camera",
            "giftId": "GIFT_CAMERA",
            "giftName": "Galaxy",
            "coinValue": 30,
            "repeatCount": 1,
        })
        scenarios.append({"id": 6, "ok": gift_camera.get("bridgeStatus") == "QUEUED", "scope": "bridge"})
        vitri = self.send_comment(name, "/vitri", None)
        scenarios.append({"id": 7, "ok": vitri.get("bridgeStatus") == "QUEUED", "scope": "bridge"})
        bait_ok = True
        last_tier = None
        for bait in BAITS:
            sent = self.send_gift({
                "username": name,
                "purpose": "bait",
                "giftId": bait["id"],
                "giftName": bait["name"],
                "coinValue": bait["coins"],
                "repeatCount": 1,
            })
            last_tier = (sent.get("viewer") or {}).get("baitTier")
            if sent.get("bridgeStatus") != "QUEUED":
                bait_ok = False
        scenarios.append({"id": 8, "ok": bait_ok and last_tier == 5, "approvedForLive": False, "scope": "bridge_mock"})
        ten = self.add_viewers(10)
        scenarios.append({"id": 9, "ok": ten.get("created") == 10, "scope": "bridge"})
        hundred = self.add_viewers(100)
        scenarios.append({"id": 10, "ok": hundred.get("created") == 100, "humanoids": 0, "scope": "logical"})
        dup_id = "dup" + secrets.token_hex(3)
        first = self.send_like(name, 1, dup_id)
        second = self.send_like(name, 1, dup_id)
        scenarios.append({
            "id": 11,
            "ok": first.get("bridgeStatus") == "QUEUED" and second.get("bridgeStatus") == "ACCEPTED_BY_BRIDGE" and (second.get("viewer") or {}).get("likes") == 501,
            "scope": "dedupe",
        })
        forced = self.force_command("ForceFishSuccess", name)
        scenarios.append({"id": 12, "ok": forced.get("appliedOnRoblox") is False and forced.get("mockOnly") is True, "scope": "mock_only"})
        passed = all(bool(row["ok"]) for row in scenarios)
        return self._result(
            "ACCEPTED_BY_BRIDGE",
            "NOT_DELIVERED",
            None,
            "",
            "Test cơ bản chạy trên bridge. Roblox không nhận vì HTTP đang tắt.",
            extra={"scenarios": scenarios, "passed": passed, "appliedOnRoblox": False, "humanoidsCreated": 0},
        )

    def send_like(self, username: Any, count: Any, event_id: Any) -> dict[str, Any]:
        row = self._require(username)
        if isinstance(row, dict) and row.get("ok") is False:
            return row
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 10000:
            return self._fail("invalid", "Số like phải từ 1 đến 10000.")
        payload: dict[str, Any] = {"count": count} if count != 1 else {}
        event = self._event("like", row["userId"], row["username"], payload, event_id)
        result = self._publish(event)
        viewer = self._public_viewer(row["userId"])
        result["viewer"] = viewer
        if viewer and result["bridgeStatus"] in {"QUEUED", "ACCEPTED_BY_BRIDGE"}:
            unlocked = "đã mở khóa câu" if viewer["fishingUnlocked"] else "chưa mở khóa câu"
            result["progress"] = f"{viewer['likes']} / 500"
            result["unlockText"] = "ĐÃ MỞ KHÓA CÂU CÁ!" if viewer["fishingUnlocked"] else ""
            result["detail"] = f"Bridge cộng {count} like. {result['progress']}. {unlocked}. Đây là phép chiếu bridge, chưa phải ACK Roblox."
        return result

    def send_comment(self, username: Any, text: Any, event_id: Any) -> dict[str, Any]:
        row = self._require(username)
        if isinstance(row, dict) and row.get("ok") is False:
            return row
        if not isinstance(text, str):
            return self._fail("invalid", "Thiếu nội dung comment.")
        cleaned = "".join(char for char in text if ord(char) >= 32).strip()
        if cleaned == "" or len(cleaned.encode("utf-8")) > 80:
            return self._fail("invalid", "Comment không hợp lệ.")
        event = self._event("comment", row["userId"], row["username"], {"text": cleaned}, event_id)
        result = self._publish(event)
        result["viewer"] = self._public_viewer(row["userId"])
        folded = normalize_comment(cleaned)
        if result["bridgeStatus"] in {"QUEUED", "ACCEPTED_BY_BRIDGE"}:
            result["detail"] = self._comment_detail(row["userId"], folded)
        return result

    def send_gift(self, body: dict[str, Any]) -> dict[str, Any]:
        row = self._require(body.get("username"))
        if isinstance(row, dict) and row.get("ok") is False:
            return row
        purpose = body.get("purpose", "normal")
        if purpose not in GIFT_PURPOSES:
            return self._fail("invalid", "Nhóm quà không hợp lệ.")
        gift_id = body.get("giftId")
        gift_name = body.get("giftName")
        coins = body.get("coinValue")
        repeat = body.get("repeatCount", 1)
        if not isinstance(gift_id, str) or not isinstance(gift_name, str):
            return self._fail("invalid", "Thiếu giftId hoặc giftName.")
        gift_id = "".join(char for char in gift_id if ord(char) >= 32).strip()
        gift_name = "".join(char for char in gift_name if ord(char) >= 32).strip()
        if gift_id == "" or gift_name == "" or len(gift_id) > 24 or len(gift_name.encode("utf-8")) > 80:
            return self._fail("invalid", "Quà không hợp lệ.")
        if isinstance(coins, bool) or not isinstance(coins, int) or coins < 0 or coins > 100000:
            return self._fail("invalid", "coinValue mock không hợp lệ.")
        if isinstance(repeat, bool) or not isinstance(repeat, int):
            return self._fail("invalid", "repeatCount không hợp lệ.")
        repeat = min(20, max(1, repeat))
        if purpose == "steal" and (gift_id != "GIFT_STEAL" or coins != 20):
            return self._fail("unconfigured", "UNCONFIGURED")
        if purpose == "camera" and (gift_id != "GIFT_CAMERA" or coins != 30):
            return self._fail("unconfigured", "UNCONFIGURED")
        if purpose == "bait" and (gift_id not in BAIT_BY_ID or BAIT_BY_ID[gift_id]["coins"] != coins):
            return self._fail("unconfigured", "UNCONFIGURED")
        payload = {
            "giftId": gift_id,
            "giftName": gift_name,
            "coinValue": coins,
            "repeatCount": repeat,
            "amount": repeat,
        }
        event = self._event("gift", row["userId"], row["username"], payload, body.get("eventId"))
        result = self._publish(event, purpose=purpose)
        result["viewer"] = self._public_viewer(row["userId"])
        if result["bridgeStatus"] in {"QUEUED", "ACCEPTED_BY_BRIDGE"}:
            result["detail"] = f"Đã xếp 1 quà mock {coins} xu ({purpose}). Không phải thanh toán TikTok. Roblox chưa ACK."
        return result

    def send_pulls(self, username: Any, count: Any) -> dict[str, Any]:
        if isinstance(count, bool) or not isinstance(count, int) or count not in {1, 10, 100}:
            return self._fail("invalid", "Chỉ tạo 1, 10 hoặc 100 PullRequest.")
        sent = 0
        last = None
        for _ in range(count):
            if self.queue.status()["depth"] >= self.queue.capacity:
                break
            last = self.send_comment(username, "/giật", None)
            if last.get("bridgeStatus") != "QUEUED":
                break
            sent += 1
        status = "QUEUED" if sent else "FAILED"
        return self._result(status, "NOT_DELIVERED", None, "", f"Đã xếp {sent} comment /giật.", extra={"sent": sent, "last": last})

    def force_command(self, kind: Any, username: Any) -> dict[str, Any]:
        if self.settings.live:
            return self._blocked()
        if kind not in FORCE_KINDS:
            return self._fail("invalid", "Lệnh force không nằm trong danh sách mock.")
        name = valid_username(username) if username else ""
        self._command_log(str(kind), name or "", "Force chỉ ghi nhận trên bridge. Không vào hàng Roblox.")
        return self._result(
            "ACCEPTED_BY_BRIDGE",
            "NOT_DELIVERED",
            None,
            "",
            "Đã nhận lệnh force ở MOCK_MODE. Không áp dụng lên Roblox vì chưa có kênh và chưa có ACK.",
            extra={"appliedOnRoblox": False, "mockOnly": True, "kind": kind},
        )

    def camera_command(self, shot: Any, username: Any) -> dict[str, Any]:
        if shot not in CAMERA_SHOTS:
            return self._fail("invalid", "Cảnh camera không hợp lệ.")
        if shot == "VITRI_FOCUS":
            result = self.send_comment(username, "/vitri", None)
            result["detail"] = "Đã xếp comment /vitri. CameraDirector trong Roblox mới quyết định có chiếu hay không. Chưa có ACK."
            return result
        if shot == "GIFT_CAMERA_CENTER":
            body = {
                "username": username,
                "purpose": "camera",
                "giftId": "GIFT_CAMERA",
                "giftName": "Camera Center",
                "coinValue": 30,
                "repeatCount": 1,
            }
            result = self.send_gift(body)
            result["detail"] = "Đã xếp 1 quà mock 30 xu. Một sự kiện, không kèm tranh cá hay đổi mồi. Roblox chưa ACK."
            return result
        self._command_log(str(shot), "", "Không có sự kiện TikTok cho cảnh này. Không tạo camera thứ hai.")
        return self._result(
            "ACCEPTED_BY_BRIDGE",
            "NOT_DELIVERED",
            None,
            "",
            "Bridge ghi nhận yêu cầu cảnh. Không gửi vào hàng Roblox vì không có sự kiện tương ứng và HTTP Roblox đang tắt. Không cắt showcase từ trang này.",
            extra={"appliedOnRoblox": False, "shot": shot},
        )

    def support_comment(self, username: Any) -> dict[str, Any]:
        fisher = self._require(username)
        if isinstance(fisher, dict) and fisher.get("ok") is False:
            return fisher
        supporter = self.add_viewer(None)
        if supporter.get("viewer") is None:
            return supporter
        result = self.send_comment(supporter["viewer"]["username"], "/giật", None)
        result["detail"] = f"Đã xếp comment hỗ trợ /giật cho @{fisher['username']}. Chưa có trạng thái showcase từ Roblox."
        return result

    def gift_steal(self, username: Any) -> dict[str, Any]:
        fisher = self._require(username)
        if isinstance(fisher, dict) and fisher.get("ok") is False:
            return fisher
        challenger = self.add_viewer(None)
        if challenger.get("viewer") is None:
            return challenger
        result = self.send_gift({
            "username": challenger["viewer"]["username"],
            "purpose": "steal",
            "giftId": "GIFT_STEAL",
            "giftName": "Rose",
            "coinValue": 20,
            "repeatCount": 1,
        })
        result["detail"] = f"Đã xếp 1 quà mock 20 xu để tranh cá với @{fisher['username']}. Không đổi tỷ lệ cá. Roblox chưa ACK."
        return result

    def preview_bait(self, tier: Any, username: Any) -> dict[str, Any]:
        if isinstance(tier, bool) or not isinstance(tier, int) or tier not in {row["tier"] for row in BAITS}:
            return self._fail("invalid", "Cấp mồi không hợp lệ.")
        bait = next(row for row in BAITS if row["tier"] == tier)
        self._command_log("preview_bait", "", bait["name"])
        return self._result(
            "ACCEPTED_BY_BRIDGE",
            "NOT_DELIVERED",
            None,
            "",
            "Xem trước danh mục mồi. Không đổi xác suất cá và không trang bị trên Roblox.",
            extra={"bait": bait, "appliedOnRoblox": False, "username": username},
        )

    def start_stress(self, kind: str, pace: float) -> dict[str, Any]:
        if self.settings.live and kind not in {"roblox_disconnected", "roblox_reconnect", "backend_restart"}:
            return self._blocked()
        if kind == "backend_restart":
            return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", "Trang này không tắt process. Khởi động lại bridge bằng tay sẽ làm mất hàng RAM.")
        if kind == "roblox_disconnected":
            label = self.roblox_label()
            return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", label, extra={"robloxLabel": label})
        if kind == "roblox_reconnect":
            label = self.roblox_label()
            detail = "Roblox vẫn chưa kéo batch." if label != "ROBLOX CONNECTED" else "Roblox vừa kéo batch. ACK chỉ hiện khi có ack thật."
            return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", detail, extra={"robloxLabel": label})
        with self.lock:
            if self.stress["running"]:
                return self._fail("busy", "Đang có bài test. Bấm dừng trước.")
            self.stress = {"running": True, "kind": kind, "sent": 0, "note": "running"}
            self.stress_stop.clear()
        worker = threading.Thread(target=self._stress_loop, args=(kind, pace), daemon=True)
        worker.start()
        return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", "Đã bắt đầu bài test có giới hạn tốc độ.", extra={"kind": kind})

    def stop_stress(self) -> dict[str, Any]:
        self.stress_stop.set()
        with self.lock:
            sent = self.stress.get("sent", 0)
            self.stress["running"] = False
            self.stress["note"] = "stopped"
        return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", None, "", "Đã dừng bài test.", extra={"sent": sent})

    def observe_publish(self, payload: dict[str, Any], status: str, sequence: int | None, purpose: str | None = None) -> None:
        event, reason = parse_event(payload)
        if event is None:
            self._append_log({
                "time": _now(),
                "eventId": clip(str(payload.get("eventId", "")), 64) if isinstance(payload, dict) else "",
                "username": "",
                "eventType": "",
                "summary": reason or "invalid",
                "bridgeStatus": "FAILED",
                "robloxStatus": "NOT_DELIVERED",
                "status": "FAILED",
                "evidence": "observed",
                "sequence": None,
            }, None)
            return
        bridge_status = "FAILED"
        roblox_status = "NOT_DELIVERED"
        if status == "created":
            bridge_status = "QUEUED"
            roblox_status = "CHƯA KÉO"
            self._apply_event(event, purpose)
        elif status == "coalesced":
            bridge_status = "ACCEPTED_BY_BRIDGE"
            roblox_status = "CHƯA KÉO"
            self._apply_event(event, purpose)
        elif status == "duplicate":
            bridge_status = "ACCEPTED_BY_BRIDGE"
            roblox_status = "NOT_DELIVERED"
        summary = _summary(event["payload"])
        if status == "duplicate":
            summary = "duplicate " + summary
        logged_sequence = sequence if status in {"created", "coalesced"} else None
        entry = {
            "time": _now(),
            "eventId": event["eventId"],
            "username": event["nickname"],
            "eventType": event["type"],
            "summary": summary,
            "bridgeStatus": bridge_status,
            "robloxStatus": roblox_status,
            "status": "FAILED" if bridge_status == "FAILED" else bridge_status,
            "evidence": "observed",
            "sequence": logged_sequence,
        }
        self._append_log(entry, logged_sequence)

    def note_pull(self, rows: list[dict[str, Any]]) -> None:
        with self.lock:
            for row in rows:
                sequence = row.get("sequence")
                entry = self.by_sequence.get(sequence)
                if entry is not None and entry["robloxStatus"] != "ACKNOWLEDGED":
                    entry["robloxStatus"] = "PULLED_BY_ROBLOX"
                    entry["status"] = "PULLED_BY_ROBLOX"
                    entry["evidence"] = "observed"

    def note_ack(self, sequences: list[int]) -> None:
        with self.lock:
            for sequence in sequences:
                entry = self.by_sequence.get(sequence)
                if entry is not None:
                    entry["robloxStatus"] = "ACKNOWLEDGED"
                    entry["status"] = "ACKNOWLEDGED"
                    entry["evidence"] = "observed"

    def _stress_loop(self, kind: str, pace: float) -> None:
        targets = {
            "join10": 10,
            "join100": 100,
            "join500": 500,
            "likes100": 100,
            "giat100": 100,
            "gifts100": 100,
            "duplicate": 2,
            "overload": self.queue.capacity + 1,
        }
        target = targets.get(kind, 0)
        sent = 0
        note = "done"
        duplicate_event = None
        try:
            while sent < target and not self.stress_stop.is_set():
                depth = self.queue.status()["depth"]
                if kind != "overload" and depth >= self.queue.capacity:
                    note = "queue_cap"
                    break
                if kind == "overload" and depth >= self.queue.capacity:
                    event = self._event("comment", "stress_drop", "stress_drop", {"text": "xin chao"}, None)
                    status, _sequence = self.queue.publish(event, rate_limit=False)
                    self.observe_publish(event, status, _sequence)
                    note = status
                    break
                event = self._stress_event(kind, sent, duplicate_event)
                if kind == "duplicate" and duplicate_event is None:
                    duplicate_event = event
                elif kind == "duplicate":
                    event = duplicate_event
                status, sequence = self.queue.publish(event, rate_limit=False)
                self.observe_publish(event, status, sequence, "normal" if kind == "gifts100" else None)
                if status == "created":
                    sent += 1
                elif status == "duplicate" and kind == "duplicate":
                    sent += 1
                    note = "duplicate"
                    break
                elif status in {"overloaded", "rate_limited"}:
                    note = status
                    break
                elif status != "coalesced":
                    note = status
                    break
                else:
                    sent += 1
                with self.lock:
                    self.stress["sent"] = sent
                if pace > 0:
                    time.sleep(pace)
        finally:
            with self.lock:
                self.stress["running"] = False
                self.stress["sent"] = sent
                self.stress["note"] = "stopped" if self.stress_stop.is_set() else note

    def _stress_event(self, kind: str, index: int, duplicate_event: dict[str, Any] | None) -> dict[str, Any]:
        if kind in {"join10", "join100", "join500"}:
            name = self._alloc_name()
            user_id = "mu" + secrets.token_hex(5)
            with self.lock:
                self.viewers[user_id] = self._blank(name, user_id)
                self.by_name[name] = user_id
            return self._event("comment", user_id, name, {"text": "join"}, None)
        if kind == "likes100":
            name = self._alloc_name()
            user_id = "mu" + secrets.token_hex(5)
            with self.lock:
                self.viewers[user_id] = self._blank(name, user_id)
                self.by_name[name] = user_id
            return self._event("like", user_id, name, {"count": 1}, None)
        if kind == "giat100":
            name = self._alloc_name()
            user_id = "mu" + secrets.token_hex(5)
            with self.lock:
                row = self._blank(name, user_id)
                row["likes"] = 500
                row["fishingUnlocked"] = True
                row["fishingEnabled"] = True
                self.viewers[user_id] = row
                self.by_name[name] = user_id
            return self._event("comment", user_id, name, {"text": "/giật"}, None)
        if kind == "gifts100":
            name = self._alloc_name()
            user_id = "mu" + secrets.token_hex(5)
            with self.lock:
                self.viewers[user_id] = self._blank(name, user_id)
                self.by_name[name] = user_id
            return self._event("gift", user_id, name, {
                "giftId": "GIFT_NORMAL",
                "giftName": "Heart",
                "coinValue": 1,
                "repeatCount": 1,
                "amount": 1,
            }, None)
        if kind == "duplicate":
            if duplicate_event is not None:
                return duplicate_event
            return self._event("like", "stress_dup", "stress_dup", {"count": 1}, "stress-dup-fixed")
        return self._event("comment", "stress_fill", "stress_fill", {"text": "xin chao"}, None)

    def _publish(self, event: dict[str, Any], purpose: str | None = None) -> dict[str, Any]:
        protected = _protected_text(event)
        if self.queue.status()["depth"] >= self.queue.capacity and protected:
            self.observe_publish(event, "overloaded", None, purpose)
            return self._result("FAILED", "NOT_DELIVERED", None, event["eventId"], "Hàng đợi đã đầy. Không xếp thêm sự kiện được bảo vệ.")
        status, sequence = self.queue.publish(event, rate_limit=False)
        self.observe_publish(event, status, sequence, purpose)
        if status == "created":
            return self._result("QUEUED", "CHƯA KÉO", sequence, event["eventId"], "Bridge đã xếp sự kiện. Roblox chưa ACK.")
        if status == "coalesced":
            return self._result("ACCEPTED_BY_BRIDGE", "CHƯA KÉO", sequence, event["eventId"], "Like được gộp vào sự kiện đang chờ. Không tạo thêm NPC.")
        if status == "duplicate":
            return self._result("ACCEPTED_BY_BRIDGE", "NOT_DELIVERED", sequence, event["eventId"], "eventId trùng. Bridge không xử lý lại.")
        return self._result("FAILED", "NOT_DELIVERED", sequence, event["eventId"], status)

    def _apply_event(self, event: dict[str, Any], purpose: str | None) -> None:
        with self.lock:
            row = self.viewers.get(event["viewerId"])
            if row is None:
                row = self._blank(event["nickname"] or event["viewerId"], event["viewerId"])
                self.viewers[event["viewerId"]] = row
                if row["username"] not in self.by_name:
                    self.by_name[row["username"]] = event["viewerId"]
            row["lastActivity"] = event["timestamp"]
            kind = event["type"]
            payload = event["payload"]
            if kind == "like":
                count = payload.get("count", 1)
                if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                    count = 1
                row["likes"] += count
                if row["likes"] >= 500:
                    row["fishingUnlocked"] = True
            elif kind == "comment":
                text = payload.get("text", "")
                folded = normalize_comment(text) if isinstance(text, str) else ""
                if folded in JOIN_TEXT and row["fishingUnlocked"]:
                    row["fishingEnabled"] = True
                if folded in PULL_TEXT:
                    row["pulls"] += 1
                    row["lastPullAt"] = event["timestamp"]
                if folded in VITRI_TEXT:
                    row["lastVitriAt"] = event["timestamp"]
            elif kind == "gift":
                row["gifts"] += 1
                gift_id = payload.get("giftId")
                if purpose == "bait" or (isinstance(gift_id, str) and gift_id in BAIT_BY_ID):
                    bait = BAIT_BY_ID.get(gift_id) if isinstance(gift_id, str) else None
                    if bait is not None and payload.get("coinValue") == bait["coins"]:
                        row["baitTier"] = bait["tier"]

    def _comment_detail(self, user_id: str, folded: str) -> str:
        with self.lock:
            row = self.viewers.get(user_id)
        if row is None:
            return "Đã gửi comment."
        if folded in JOIN_TEXT:
            if row["fishingUnlocked"] and row["fishingEnabled"]:
                return "Bridge đã xếp lệnh câu. Viewer đủ 500 like trong phép chiếu bridge. Roblox chưa ACK."
            return "Bridge đã xếp lệnh câu. Viewer chưa đủ 500 like trong phép chiếu bridge. Roblox chưa ACK."
        if folded in PULL_TEXT:
            gap = ""
            if row["pulls"] > 1 and row["lastPullAt"]:
                gap = " Có hơn một /giật trong phiên bridge."
            rig = " Chưa có dữ liệu rig Roblox."
            return "Bridge đã xếp /giật." + gap + rig + " Cooldown 20 giây do Roblox quyết định."
        if folded in VITRI_TEXT:
            return "Bridge đã xếp /vitri. Cooldown 300 giây và camera 5 giây do CameraDirector quyết định. Chưa có ACK."
        return "Bridge đã xếp comment. Roblox chưa ACK."

    def _require(self, username: Any) -> dict[str, Any]:
        name = valid_username(username)
        if name is None:
            return self._fail("invalid", "Chọn một viewer.")
        with self.lock:
            user_id = self.by_name.get(name)
            if user_id is None:
                return self._fail("missing", "Không thấy viewer.")
            return dict(self.viewers[user_id])

    def _public_viewer(self, user_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.viewers.get(user_id)
            if row is None:
                return None
            return {
                "username": row["username"],
                "userId": row["userId"],
                "likes": row["likes"],
                "fishingUnlocked": row["fishingUnlocked"],
                "fishingEnabled": row["fishingEnabled"],
                "totalScore": None,
                "biggestFish": None,
                "lastActivity": row["lastActivity"],
                "gifts": row["gifts"],
                "baitTier": row["baitTier"],
                "avatarUrl": row.get("avatarUrl") or "",
                "mockPoints": row.get("mockPoints", 0),
                "source": "bridge",
            }

    def _alloc_name(self) -> str:
        with self.lock:
            while True:
                self.name_serial += 1
                name = f"mock_{self.name_serial:04d}"
                if name not in self.by_name:
                    return name

    def _blank(self, username: str, user_id: str) -> dict[str, Any]:
        return {
            "username": username,
            "userId": user_id,
            "likes": 0,
            "fishingUnlocked": False,
            "fishingEnabled": False,
            "lastActivity": _now(),
            "gifts": 0,
            "baitTier": None,
            "avatarUrl": "",
            "mockPoints": 0,
            "pulls": 0,
            "lastPullAt": 0.0,
            "lastVitriAt": 0.0,
        }

    def _event(self, kind: str, user_id: str, username: str, payload: dict[str, Any], event_id: Any) -> dict[str, Any]:
        if isinstance(event_id, str) and event_id:
            chosen = event_id
        else:
            chosen = kind[:1] + secrets.token_hex(8)
        nickname = username if username else user_id
        return {
            "eventId": chosen[:64],
            "type": kind,
            "viewerId": user_id[:64],
            "nickname": nickname[:80],
            "timestamp": round(_now(), 3),
            "payload": payload,
        }

    def _append_log(self, entry: dict[str, Any], sequence: int | None) -> None:
        with self.lock:
            self.log.appendleft(entry)
            if sequence is not None:
                self.by_sequence[sequence] = entry
                if len(self.by_sequence) > 4000:
                    stale = list(self.by_sequence)[:1000]
                    for key in stale:
                        self.by_sequence.pop(key, None)

    def _command_log(self, kind: str, username: str, summary: str) -> None:
        entry = {
            "time": _now(),
            "eventId": "host-" + secrets.token_hex(4),
            "username": username,
            "eventType": kind,
            "summary": clip(summary, 80),
            "bridgeStatus": "ACCEPTED_BY_BRIDGE",
            "robloxStatus": "NOT_DELIVERED",
            "status": "ACCEPTED_BY_BRIDGE",
            "evidence": "observed",
            "sequence": None,
        }
        self._append_log(entry, None)

    def _result(self, bridge: str, roblox: str, sequence: int | None, event_id: str, detail: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {
            "ok": bridge != "FAILED",
            "bridgeStatus": bridge,
            "robloxStatus": roblox,
            "robloxLabel": self.roblox_label(),
            "sequence": sequence,
            "eventId": event_id,
            "depth": self.queue.status()["depth"],
            "detail": detail,
            "appliedOnRoblox": False,
        }
        if extra:
            body.update(extra)
        return body

    def _fail(self, code: str, detail: str) -> dict[str, Any]:
        return {**self._result("FAILED", "NOT_DELIVERED", None, "", detail), "ok": False, "error": code}

    def _blocked(self) -> dict[str, Any]:
        return {**self._fail("live_blocked", "MOCK_MODE đang tắt. Live không nhận lệnh giả."), "statusCode": 409}


def _summary(payload: dict[str, Any]) -> str:
    parts = []
    for key, value in payload.items():
        if key.lower() in {"avatar", "image", "thumbnail", "bytes"}:
            continue
        if isinstance(value, str) and len(value) > 40:
            value = value[:40]
        parts.append(f"{key}={value}")
    return clip(" ".join(parts), 80)


def _protected_text(event: dict[str, Any]) -> bool:
    if event["type"] == "gift":
        return True
    if event["type"] != "comment":
        return False
    text = event["payload"].get("text", "")
    if not isinstance(text, str):
        return False
    return normalize_comment(text) in {"cau", "câu"}


def origin_allowed(origin: str, port: int) -> bool:
    if origin == "http://testserver":
        return True
    parsed = urlsplit(origin)
    if parsed.scheme != "http":
        return False
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost"}:
        return False
    return parsed.port in {None, port}


def host_allowed(host_header: str) -> bool:
    host = host_header.split(",")[0].strip().lower()
    if host in {"testserver", "localhost", "127.0.0.1", "[::1]"}:
        return True
    name = host
    if host.startswith("["):
        name = host.split("]")[0] + "]"
    else:
        name = host.rsplit(":", 1)[0]
    return name in {"localhost", "127.0.0.1", "[::1]"}
