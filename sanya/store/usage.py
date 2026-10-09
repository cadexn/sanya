"""Counts of calls to outside services, per month.

Used for the monthly search caps. The counts are for the whole bot, not one
server, so they live in one small file rather than in a server's database.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class Usage:
    def __init__(self, path: Path):
        self._path = Path(path)

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def count(self, service: str, month: str) -> int:
        """Calls made to ``service`` in ``month`` (written as 2026-10)."""
        value = self._read().get(month, {}).get(service, 0)
        return value if isinstance(value, int) else 0

    def add(self, service: str, month: str) -> int:
        data = self._read()
        counts = data.setdefault(month, {})
        counts[service] = self.count(service, month) + 1
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self._path)
        return counts[service]
