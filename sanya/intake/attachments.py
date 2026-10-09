"""Files: taking them in, checking what they really are, keeping them, and showing them to the model.

A file is judged by its bytes, not by its name or the type Discord declares.
The bot never deletes a stored file; the owner clears the folder by hand. She
can open a file for a set number of hours, and only in the channel it came from.
"""
from __future__ import annotations

import base64
import io
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import events
from ..config import Attachments, Files, Tools
from ..store.db import Store
from ..tools.fetch import FetchError, fetch

IMAGE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}
_ENDING_FOR = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
               "application/pdf": ".pdf", "text/plain": ".txt"}

# What the model is told when a file cannot be used. She puts it in her own words.
REASONS = {
    "too_many": "only the first {max_count} files of a message are accepted",
    "too_large": "it is over the {max_mb} MB limit",
    "unsupported_type": "this kind of file cannot be opened; images, GIFs, text files and PDFs can",
    "unreadable": "the file is damaged, or is not what its name says",
    "too_big_image": "the picture is too large to look at",
    "download_failed": "it could not be downloaded",
}


class Unusable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class FileUnavailable(Exception):
    """She may not, or cannot, open this file. ``kind`` is what the model is told."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind  # no_such_file, too_old, file_gone, no_text, unreadable


@dataclass(frozen=True)
class Stored:
    file_id: int
    filename: str
    mime: str
    size: int
    path: Path
    seen: bool = False  # already shown to her as an overheard picture

    @property
    def is_image(self) -> bool:
        return self.mime.startswith("image/")


@dataclass(frozen=True)
class Refused:
    filename: str
    reason: str


def media_type(data: bytes, filename: str, limits: Files) -> str:
    """What ``data`` really is. Raises Unusable for anything she cannot open."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")) or (
            data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        from PIL import Image
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in IMAGE_FORMATS:
                    raise Unusable("unsupported_type")
                if (image.width * image.height > limits.max_megapixels * 1_000_000
                        or getattr(image, "n_frames", 1) > limits.max_gif_frames):
                    raise Unusable("too_big_image")
                mime = IMAGE_FORMATS[image.format]
                image.verify()
        except Unusable:
            raise
        except Exception:
            raise Unusable("unreadable") from None
        return mime
    if filename.lower().endswith(tuple(limits.text_endings)):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise Unusable("unreadable") from None
        if any(ord(c) < 32 and c not in "\r\n\t" for c in text):
            raise Unusable("unreadable")
        return "text/plain"
    raise Unusable("unsupported_type")


def _label(filename: str) -> str:
    """A filename made safe to show the model inside a bracketed note."""
    return re.sub(r"[\[\]\r\n]", " ", filename).strip()[:60] or "file"


def describe(stored: Stored) -> str:
    """The note in a member's message that tells the model a file came with it."""
    name = _label(stored.filename)
    if stored.mime == "image/gif":
        return f"[attached GIF: {name}, file {stored.file_id}, shown as still frames]"
    if stored.is_image:
        return f"[attached image: {name}, file {stored.file_id}]"
    what = "PDF" if stored.mime == "application/pdf" else "text file"
    return f"[attached {what}: {name}, file {stored.file_id}; use open_file to read it]"


def describe_refused(refused: Refused, settings: Attachments) -> str:
    reason = REASONS[refused.reason].format(max_count=settings.max_count, max_mb=settings.max_mb)
    return f"[attached file {_label(refused.filename)} could not be used: {reason}]"


class FileStore:
    def __init__(self, data_dir: Path, store: Store, attachments: Attachments, files: Files, tools: Tools,
                 fetcher: Callable[..., Any] = fetch, clock: Callable[[], float] = time.time):
        self._root = Path(data_dir) / "files"
        self._store = store
        self._attachments = attachments
        self._files = files
        self._identity = tools.browser_identity
        self._max_redirects = tools.max_redirects
        self._fetch = fetcher
        self._clock = clock
        self._total: int | None = None  # bytes under the folder, counted on first use
        self._reminded = False

    def _stored(self, row) -> Stored:
        return Stored(row["id"], row["filename"], row["mime"], row["size"], self._root / row["path"],
                      row["shown_at"] is not None)

    def _write(self, guild_id: str, channel_id: str, message_id: str, position: int,
               filename: str, mime: str, data: bytes, now: float) -> str:
        moment = datetime.fromtimestamp(now)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).stem)[:60] or "file"
        name = f"{moment:%H%M%S}_{channel_id}_{message_id}_{position}_{safe}{_ENDING_FOR[mime]}"
        relative = Path(guild_id) / f"{moment:%Y-%m-%d}" / name
        (self._root / relative).parent.mkdir(parents=True, exist_ok=True)
        (self._root / relative).write_bytes(data)
        self._count(len(data))
        return relative.as_posix()

    def _count(self, added: int) -> None:
        if self._total is None:
            self._total = sum(os.path.getsize(os.path.join(folder, name))
                              for folder, _, names in os.walk(self._root) for name in names)
        else:
            self._total += added
        limit = self._files.reminder_gb * 1024 ** 3
        if self._total > limit and not self._reminded:
            # The owner clears this folder by hand; this is the nudge to do it.
            self._reminded = True
            events.log_event("files_folder_large", gb=round(self._total / 1024 ** 3, 1))

    async def take(self, guild_id: str, channel_id: str, message_id: str, author_id: str,
                   attachments: Iterable[Any], *, kind: str = "sent",
                   images_only: bool = False) -> tuple[list[Stored], list[Refused]]:
        """Download, check and keep a message's files. Safe to call twice for one message.

        ``images_only`` is for files she was not sent herself: anything that is
        not a picture is passed over without comment.
        """
        conn = self._store.guild(guild_id)
        max_bytes = self._attachments.max_mb * 1024 * 1024
        kept: list[Stored] = []
        refused: list[Refused] = []
        for position, attachment in enumerate(attachments):
            filename = str(getattr(attachment, "filename", "") or "file")
            if position >= self._attachments.max_count:
                refused.append(Refused(filename, "too_many"))
                continue
            row = conn.execute("SELECT * FROM files WHERE message_id = ? AND position = ?",
                               (message_id, position)).fetchone()
            if row is not None:
                kept.append(self._stored(row))
                continue
            try:
                if (getattr(attachment, "size", 0) or 0) > max_bytes:
                    raise Unusable("too_large")
                fetched = await self._fetch(attachment.url, max_bytes=max_bytes,
                                            timeout=self._attachments.download_seconds,
                                            user_agent=self._identity, max_redirects=self._max_redirects)
                mime = media_type(fetched.data, filename, self._files)
            except FetchError as exc:
                refused.append(Refused(filename, "too_large" if exc.kind == "too_large" else "download_failed"))
                continue
            except Unusable as exc:
                refused.append(Refused(filename, exc.reason))
                continue
            if images_only and not mime.startswith("image/"):
                continue
            now = self._clock()
            path = self._write(guild_id, channel_id, message_id, position, filename, mime, fetched.data, now)
            cursor = conn.execute(
                "INSERT INTO files (channel_id, message_id, position, author_id, kind, filename, mime, size, path,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (channel_id, message_id, position, author_id, kind, filename, mime, len(fetched.data), path, now))
            kept.append(Stored(cursor.lastrowid, filename, mime, len(fetched.data), self._root / path))
            events.log_event("file_stored", guild_id=guild_id, channel_id=channel_id, file_id=cursor.lastrowid,
                             kind=kind, mime=mime, bytes=len(fetched.data))
        if images_only:
            refused = []
        return kept, refused

    def for_messages(self, guild_id: str, message_ids: list[str]) -> dict[str, dict[int, Stored]]:
        """The stored files of some messages: message id -> position in the message -> file."""
        found: dict[str, dict[int, Stored]] = {}
        conn = self._store.guild(guild_id)
        for start in range(0, len(message_ids), 500):
            chunk = message_ids[start:start + 500]
            marks = ", ".join("?" * len(chunk))
            for row in conn.execute(f"SELECT * FROM files WHERE message_id IN ({marks})", chunk):
                found.setdefault(row["message_id"], {})[row["position"]] = self._stored(row)
        return found

    def mark_shown(self, guild_id: str, file_ids: Iterable[int], at: float) -> None:
        """Record that these overheard pictures have been shown to her, so they are not shown again."""
        conn = self._store.guild(guild_id)
        for file_id in file_ids:
            conn.execute("UPDATE files SET shown_at = ? WHERE id = ? AND shown_at IS NULL", (at, file_id))

    def find(self, guild_id: str, channel_id: str, file_id: Any) -> Stored:
        """A file she may open now: from this channel, recent enough, and still on disk."""
        try:
            number = int(file_id)
        except (TypeError, ValueError):
            raise FileUnavailable("no_such_file") from None
        row = self._store.guild(guild_id).execute("SELECT * FROM files WHERE id = ?", (number,)).fetchone()
        if row is None or row["channel_id"] != channel_id:
            raise FileUnavailable("no_such_file")
        if row["created_at"] < self._clock() - self._attachments.open_hours * 3600:
            raise FileUnavailable("too_old")
        stored = self._stored(row)
        if not stored.path.is_file():
            raise FileUnavailable("file_gone")
        return stored

    def pictures(self, stored: Stored) -> list[str]:
        """The picture as the model is shown it: full size, a GIF as a few evenly spaced frames."""
        from PIL import Image

        def url(mime: str, data: bytes) -> str:
            return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")

        if stored.mime in ("image/png", "image/jpeg"):
            return [url(stored.mime, stored.path.read_bytes())]
        shown = []
        with Image.open(stored.path) as image:
            total = getattr(image, "n_frames", 1)
            wanted = min(self._files.gif_frames, total) if stored.mime == "image/gif" else 1
            indexes = sorted({round(i * (total - 1) / (wanted - 1)) for i in range(wanted)}) if wanted > 1 else [0]
            for index in indexes:
                image.seek(index)
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, "PNG")
                shown.append(url("image/png", buffer.getvalue()))
        return shown

    def text(self, stored: Stored, budget: int) -> str:
        """The text of a text file or a PDF, up to a little past ``budget`` characters."""
        if stored.mime == "text/plain":
            return stored.path.read_bytes().decode("utf-8-sig", errors="replace")
        if stored.mime != "application/pdf":
            raise FileUnavailable("unreadable")
        from pypdf import PdfReader
        try:
            parts, length = [], 0
            for page in PdfReader(str(stored.path)).pages:
                part = (page.extract_text() or "").strip()
                if part:
                    parts.append(part)
                    length += len(part)
                if length > budget:
                    break
        except Exception:
            raise FileUnavailable("unreadable") from None
        if not parts:
            raise FileUnavailable("no_text")
        return "\n\n".join(parts)
