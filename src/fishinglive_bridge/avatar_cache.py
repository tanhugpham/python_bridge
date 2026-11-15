"""RAM-only 64x64 viewer thumbnails. Nothing is written to disk."""

from __future__ import annotations

import base64
import struct
import zlib
from collections import OrderedDict


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode_png(rgba: bytes, width: int, height: int) -> bytes:
    rows = []
    stride = width * 4
    for y in range(height):
        rows.append(b"\x00" + rgba[y * stride : (y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + _chunk(b"IEND", b"")


def downsample_png(rgba: bytes, width: int, height: int, target: int = 64) -> bytes:
    if width < 1 or height < 1 or len(rgba) != width * height * 4:
        raise ValueError("rgba")
    fitted = bytearray(target * target * 4)
    for y in range(target):
        source_y = min(height - 1, y * height // target)
        for x in range(target):
            source_x = min(width - 1, x * width // target)
            start = (source_y * width + source_x) * 4
            dest = (y * target + x) * 4
            fitted[dest : dest + 4] = rgba[start : start + 4]
    return encode_png(bytes(fitted), target, target)


class AvatarCache:
    """At most 100 thumbnails. A viewer is fetched once per session."""

    def __init__(self, limit: int = 100) -> None:
        self.limit = limit
        self.items: OrderedDict[str, bytes] = OrderedDict()
        self.joins: set[str] = set()
        self.stores = 0

    def note_join(self, viewer_id: str) -> None:
        # Joining records interest. It does not download and does not write a file.
        if viewer_id:
            self.joins.add(viewer_id)

    def store(self, viewer_id: str, rgba: bytes, width: int, height: int) -> bytes:
        existing = self.items.get(viewer_id)
        if existing is not None:
            self.items.move_to_end(viewer_id)
            return existing
        png = downsample_png(rgba, width, height, 64)
        self.items[viewer_id] = png
        self.stores += 1
        self.joins.add(viewer_id)
        while len(self.items) > self.limit:
            self.items.popitem(last=False)
        return png

    def get(self, viewer_id: str) -> bytes | None:
        image = self.items.get(viewer_id)
        if image is not None:
            self.items.move_to_end(viewer_id)
        return image

    def viewers(self) -> list[dict[str, str]]:
        return [
            {"viewerId": viewer_id, "pngBase64": base64.b64encode(image).decode("ascii")}
            for viewer_id, image in self.items.items()
        ]

    def clear(self) -> None:
        self.items.clear()
        self.joins.clear()
        self.stores = 0
