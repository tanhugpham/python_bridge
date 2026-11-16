"""Phase 9H helper: HTTP reverse proxy with a controllable outage.

The mirror posts to this loopback proxy instead of Render directly, so a test can cut the
mirror's upstream link for 30-120 s while the local bridge keeps accepting events.

    python phase9h-proxy.py --port 8799 --origin https://python-bridge-6v9w.onrender.com

Modes:
    POST /__control {"mode": "down"}  -> every later request is dropped (connection reset)
    POST /__control {"mode": "up"}    -> traffic flows again
    GET  /__control                   -> counters

No credential is stored: headers are forwarded verbatim, nothing is logged.
"""

from __future__ import annotations

import argparse
import http.client
import json
import ssl
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {
    "mode": "up",
    "requests": 0,
    "forwarded": 0,
    "dropped": 0,
    "errors": 0,
    "startedAt": time.time(),
    "lastPath": "",
}
LOCK = threading.Lock()


def make_handler(origin: str):
    parts = urllib.parse.urlsplit(origin)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    secure = parts.scheme == "https"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep the console clean for the test driver
            return

        def _control(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != "/__control":
                return
            if self.command == "GET":
                with LOCK:
                    body = dict(STATE)
                self._reply(200, json.dumps(body).encode("utf-8"), "application/json")
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except Exception:
                payload = {}
            mode = payload.get("mode") if isinstance(payload, dict) else None
            if mode in {"up", "down"}:
                with LOCK:
                    STATE["mode"] = mode
            with LOCK:
                body = dict(STATE)
            self._reply(200, json.dumps(body).encode("utf-8"), "application/json")

        def _reply(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _forward(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/__control":
                self._control()
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            with LOCK:
                STATE["requests"] += 1
                STATE["lastPath"] = self.path
                # Only the mirror's own endpoints are cut. My health probe is a separate
                # client link and must stay usable while the mirror is offline.
                down = STATE["mode"] == "down" and parsed.path in {"/v1/events", "/v1/acks"}
            if down:
                with LOCK:
                    STATE["dropped"] += 1
                # A real network loss: close the socket without a response.
                try:
                    self.connection.close()
                except Exception:
                    pass
                return
            headers = {}
            for name in ("X-Bridge-Key", "Content-Type", "Accept"):
                value = self.headers.get(name)
                if value:
                    headers[name] = value
            target = parsed.path + (("?" + parsed.query) if parsed.query else "")
            try:
                if secure:
                    connection = http.client.HTTPSConnection(
                        host, port, timeout=20.0, context=ssl.create_default_context()
                    )
                else:
                    connection = http.client.HTTPConnection(host, port, timeout=20.0)
                connection.request(self.command, target, body=body, headers=headers)
                response = connection.getresponse()
                payload = response.read()
                content_type = response.getheader("Content-Type") or "application/json"
                status = response.status
                connection.close()
                with LOCK:
                    STATE["forwarded"] += 1
                self._reply(status, payload, content_type)
            except Exception:
                with LOCK:
                    STATE["errors"] += 1
                try:
                    self.connection.close()
                except Exception:
                    pass

        def do_GET(self) -> None:
            self._forward()

        def do_POST(self) -> None:
            self._forward()

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--origin", default="https://python-bridge-6v9w.onrender.com")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.origin))
    print(f"proxy 127.0.0.1:{args.port} -> {args.origin}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
