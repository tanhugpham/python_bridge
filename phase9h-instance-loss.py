"""Phase 9H scenario C: the queue loses its memory with 30 unacked rows.

Stops relying on a real Render redeploy (pushes did not trigger a build in this session);
`POST /v1/session/reset` reproduces exactly what a new instance looks like to the mirror:
a new session id, an empty queue, an empty dedup set and an empty ack history.

Run with Play stopped. Writes `phase9h-out/scenario-instance-loss.json`.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "phase9h-out"


def env_value(name: str) -> str:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    return ""


INGEST = env_value("BRIDGE_INGEST_KEY")
LOCAL = "http://127.0.0.1:8787"
PROXY = "http://127.0.0.1:8799"


def get(url: str, timeout: float = 60.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post(url: str, body: dict, key: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Bridge-Key"] = key
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def mirror_fields(board: dict) -> dict:
    keys = (
        "queued", "unsent", "sent", "created", "duplicates", "acked",
        "sessionId", "resetEpoch", "sessionChanges", "replayed", "lastError",
    )
    return {key: board["mirror"][key] for key in keys}


def main() -> int:
    stamp = int(time.time())
    for index in range(30):
        post(LOCAL + "/control/events", {
            "eventId": "h-inst-%d-%03d" % (stamp, index),
            "type": "like",
            "viewerId": "in%03d" % index,
            "nickname": "in%03d" % index,
            "timestamp": time.time(),
            "payload": {"count": 4},
        })
    time.sleep(8)
    before_board = get(LOCAL + "/host/metrics", timeout=10)
    before_render = get(PROXY + "/v1/health", timeout=90)
    reset = post(PROXY + "/v1/session/reset", {"confirm": True}, INGEST)
    time.sleep(10)
    after_board = get(LOCAL + "/host/metrics", timeout=10)
    after_render = get(PROXY + "/v1/health", timeout=90)
    report = {
        "stamp": stamp,
        "before": {"mirror": mirror_fields(before_board), "render": before_render},
        "reset": reset,
        "after": {"mirror": mirror_fields(after_board), "render": after_render},
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "scenario-instance-loss.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
