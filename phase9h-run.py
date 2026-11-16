"""Phase 9H driver: outage 60 s, producer restart, and evidence capture.

It owns the local bridge process (starts it, kills it, starts it again), simulates the
network loss with the loopback proxy, and writes JSON evidence next to this file.

    python phase9h-run.py            # runs everything, prints progress on stdout

No credential is printed or written: keys are read from .env and only sent as headers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "phase9h-out"
PROXY = "http://127.0.0.1:8799"
LOCAL = "http://127.0.0.1:8787"
STAMP = int(time.time() * 1000)


def env_value(name: str) -> str:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    return ""


INGEST = env_value("BRIDGE_INGEST_KEY")
READ = env_value("BRIDGE_READ_KEY")


def get(url: str, headers: dict | None = None, timeout: float = 15.0):
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post(url: str, body: dict, headers: dict | None = None, timeout: float = 15.0):
    data = json.dumps(body).encode("utf-8")
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=merged, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, {"error": error.read().decode("utf-8", errors="replace")}


def control(mode: str) -> dict:
    return post(PROXY + "/__control", {"mode": mode}, timeout=10.0)[1]


def proxy_state() -> dict:
    return get(PROXY + "/__control", timeout=10.0)


def publish(event_id: str, kind: str, viewer: str, payload: dict, timestamp: float) -> dict:
    body = {
        "eventId": event_id,
        "type": kind,
        "viewerId": viewer,
        "nickname": viewer,
        "timestamp": timestamp,
        "payload": payload,
    }
    status, answer = post(LOCAL + "/control/events", body, timeout=10.0)
    return {"http": status, **answer}


def metrics() -> dict:
    return get(LOCAL + "/host/metrics", timeout=10.0)


def render_health() -> dict:
    return get(PROXY + "/v1/health", timeout=90.0)


def render_depth() -> int:
    return int(render_health().get("depth") or 0)


def start_bridge() -> subprocess.Popen:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = "src"
    log = (OUT / ("bridge-%d.out.txt" % STAMP)).open("wb")
    process = subprocess.Popen(
        [str(ROOT / ".venv313" / "Scripts" / "python.exe"), "-m", "fishinglive_bridge"],
        cwd=str(ROOT),
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            get(LOCAL + "/v1/health", timeout=2.0)
            return process
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("local bridge did not start")


def wait_until(check, timeout: float, label: str) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if check():
                return True
        except Exception:
            pass
        time.sleep(0.25)
    print("TIMEOUT waiting for " + label, flush=True)
    return False


def sample_baseline() -> dict:
    board = metrics()
    return {
        "at": time.time(),
        "queueDepth": board["queue"]["depth"],
        "mirrorQueued": board["mirror"]["queued"],
        "mirrorSent": board["mirror"]["sent"],
        "mirrorAcked": board["mirror"]["acked"],
        "mirrorCreated": board["mirror"]["created"],
        "render": render_health(),
        "proxy": proxy_state(),
    }


def main() -> int:
    OUT.mkdir(exist_ok=True)
    results: dict = {"stamp": STAMP, "scenarios": {}}
    bridge: subprocess.Popen | None = None
    try:
        control("up")
        bridge = start_bridge()
        results["bridgeStartSec"] = round(time.time(), 3)
        results["baseline"] = sample_baseline()
        print("baseline %s" % json.dumps(results["baseline"]["render"]), flush=True)

        # ---- A: warm-up ----------------------------------------------------
        for index in range(10):
            publish("h-warm-%d-%03d" % (STAMP, index), "like", "hw%02d" % index, {"count": 1}, time.time())
        ok = wait_until(lambda: metrics()["mirror"]["queued"] == 0, 40.0, "warm drain")
        results["scenarios"]["warm"] = {"ok": ok, "mirror": metrics()["mirror"]}

        # ---- B: 60 s outage ------------------------------------------------
        control("down")
        outage_started = time.time()
        published = 0
        while time.time() - outage_started < 60.0:
            publish(
                "h-out-%d-%03d" % (STAMP, published),
                "comment",
                "ho%03d" % (published % 50),
                {"text": "xin chao"},
                time.time(),
            )
            published += 1
            time.sleep(1.0)
        during = metrics()["mirror"]
        depth_during = render_depth()
        control("up")
        recovered_at = time.time()
        drained = wait_until(lambda: metrics()["mirror"]["queued"] == 0, 150.0, "outage drain")
        drain_sec = round(time.time() - recovered_at, 1)
        after = metrics()["mirror"]
        results["scenarios"]["outage"] = {
            "seconds": 60,
            "published": published,
            "during": {key: during[key] for key in ("queued", "deferred", "failures", "sent", "lastError", "durable")},
            "renderDepthDuring": depth_during,
            "drained": drained,
            "drainSec": drain_sec,
            "after": {key: after[key] for key in ("queued", "acked", "sent", "created", "duplicates", "failed", "expired", "ackLatencyMs", "lastError")},
            "proxyAfter": proxy_state(),
        }
        print("outage %s" % json.dumps(results["scenarios"]["outage"], ensure_ascii=False), flush=True)

        # ---- C: producer restart with unacked rows -------------------------
        waiting_before = metrics()["mirror"]["queued"]
        control("down")
        for index in range(30):
            publish("h-rst-%d-%03d" % (STAMP, index), "like", "hr%03d" % index, {"count": 2}, time.time())
        queued_ok = wait_until(lambda: metrics()["mirror"]["queued"] >= waiting_before + 30, 30.0, "restart backlog")
        backlog = metrics()["mirror"]["queued"]
        bridge.kill()
        bridge.wait(timeout=20)
        time.sleep(3.0)
        control("up")
        bridge = start_bridge()
        restarted_at = time.time()
        recovered = wait_until(lambda: metrics()["mirror"]["queued"] == 0, 150.0, "restart drain")
        board = metrics()
        results["scenarios"]["restart"] = {
            "backlogBeforeKill": backlog,
            "queuedOk": queued_ok,
            "recovered": recovered,
            "recoverSec": round(time.time() - restarted_at, 1),
            "mirror": {key: board["mirror"][key] for key in ("queued", "sent", "acked", "replayed", "failed", "expired", "durable", "sessionChanges")},
            "localQueue": board["queue"],
            "render": render_health(),
        }
        print("restart %s" % json.dumps(results["scenarios"]["restart"], ensure_ascii=False), flush=True)

        results["final"] = {"mirror": metrics()["mirror"], "render": render_health()}
        print("DONE", flush=True)
        return 0
    finally:
        (OUT / "phase9h-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        if bridge is not None and bridge.poll() is None:
            bridge.terminate()


if __name__ == "__main__":
    sys.exit(main())

