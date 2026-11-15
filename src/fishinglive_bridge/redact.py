"""Log formatting that removes known secrets and caps ordinary lines."""

from __future__ import annotations

import logging
import time
from collections import deque


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: list[str], fmt: str) -> None:
        super().__init__(fmt)
        self._secrets = [item for item in secrets if item]

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


class InfoRateFilter(logging.Filter):
    """Keep warnings. Ordinary lines stop after 60 per minute."""

    def __init__(self, limit: int = 60) -> None:
        super().__init__()
        self.limit = limit
        self.hits: deque[float] = deque()

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno > logging.INFO:
            return True
        now = time.monotonic()
        while self.hits and now - self.hits[0] >= 60:
            self.hits.popleft()
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(now)
        return True


def configure_logging(secrets: list[str]) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(secrets, "%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(InfoRateFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("TikTokLive").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").disabled = True
