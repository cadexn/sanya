"""Small previews of the pictures she kept, made in memory. Nothing is written to disk.

A kept picture can be many megabytes. The page shows a row of them, so each is shrunk here once
and the small copy held for as long as the monitor runs.
"""
from __future__ import annotations

import io
from collections import OrderedDict
from typing import Hashable, Optional

from PIL import Image, ImageOps

SIDE = 360  # the longest side of a preview, in pixels
KEPT = 300  # previews held in memory
BEHIND = (34, 37, 47)  # what shows through a see-through picture


def shrink(data: bytes) -> Optional[bytes]:
    """A small JPEG of a picture, or None if it cannot be read as one. An animation gives its first frame."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            image = ImageOps.exif_transpose(image)
            image.thumbnail((SIDE, SIDE))
            if image.mode != "RGB":
                solid = image.convert("RGBA")
                image = Image.new("RGB", solid.size, BEHIND)
                image.paste(solid, mask=solid.getchannel("A"))
            out = io.BytesIO()
            image.save(out, "JPEG", quality=82)
            return out.getvalue()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None


class Previews:
    def __init__(self) -> None:
        self._kept: OrderedDict[Hashable, Optional[bytes]] = OrderedDict()

    def of(self, key: Hashable, data: bytes) -> Optional[bytes]:
        """The preview of ``data``, made once for each ``key``."""
        if key in self._kept:
            self._kept.move_to_end(key)
            return self._kept[key]
        small = shrink(data)
        self._kept[key] = small
        while len(self._kept) > KEPT:
            self._kept.popitem(last=False)
        return small

    def has(self, key: Hashable) -> bool:
        return key in self._kept
