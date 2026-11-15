"""Environment settings. Values stay out of source and out of logs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _required(name: str, minimum: int = 1) -> str:
    value = os.environ.get(name, "").strip()
    if len(value) < minimum:
        raise RuntimeError(f"{name} is missing")
    return value


@dataclass(frozen=True)
class Settings:
    mode: str
    host: str
    port: int
    ingest_key: str
    read_key: str
    queue_path: Path
    ssl_cert: str
    ssl_key: str
    tiktok_username: str
    session_id: str
    target_idc: str
    max_backoff: float = 60.0
    batch_limit: int = 20

    @property
    def live(self) -> bool:
        return self.mode == "live"

    @property
    def loopback(self) -> bool:
        return self.host in {"127.0.0.1", "localhost", "::1"}

    def secret_values(self) -> list[str]:
        return [self.ingest_key, self.read_key, self.session_id]


def load_settings(env_file: Path | None = None) -> Settings:
    if env_file is not None:
        load_dotenv(env_file)
    # Render terminates HTTPS at its proxy and forwards HTTP to this service.
    # Local and other deployments continue to require TLS for public binds.
    on_render = os.environ.get("RENDER", "").strip().lower() == "true"
    mode = os.environ.get("BRIDGE_MODE", "mock").strip().lower()
    if mode not in {"mock", "live"}:
        raise RuntimeError("BRIDGE_MODE must be mock or live")
    default_host = "0.0.0.0" if on_render else "127.0.0.1"
    host = os.environ.get("BRIDGE_HOST", default_host).strip() or default_host
    port = int(os.environ.get("BRIDGE_PORT") or (os.environ.get("PORT", "10000") if on_render else "8787"))
    ssl_cert = os.environ.get("BRIDGE_SSL_CERT", "").strip()
    ssl_key = os.environ.get("BRIDGE_SSL_KEY", "").strip()
    settings = Settings(
        mode=mode,
        host=host,
        port=port,
        ingest_key=_required("BRIDGE_INGEST_KEY", 16),
        read_key=_required("BRIDGE_READ_KEY", 16),
        queue_path=Path(os.environ.get("BRIDGE_QUEUE_PATH", "data/queue.json")),
        ssl_cert=ssl_cert,
        ssl_key=ssl_key,
        tiktok_username=os.environ.get("TIKTOK_USERNAME", "").strip().lstrip("@"),
        session_id=os.environ.get("TIKTOK_SESSION_ID", "").strip(),
        target_idc=os.environ.get("TIKTOK_TARGET_IDC", "").strip(),
    )
    if settings.live and not settings.tiktok_username:
        raise RuntimeError("TIKTOK_USERNAME is required in live mode")
    if settings.session_id and not settings.target_idc:
        raise RuntimeError("TIKTOK_TARGET_IDC is required when a session is set")
    if (settings.ssl_cert or settings.ssl_key) and not (settings.ssl_cert and settings.ssl_key):
        raise RuntimeError("BRIDGE_SSL_CERT and BRIDGE_SSL_KEY must be set together")
    if not settings.loopback and not settings.ssl_cert and not on_render:
        raise RuntimeError("a public bind requires TLS certificate files")
    return settings
