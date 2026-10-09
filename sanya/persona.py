"""Loads the persona: who she is, the fixed lines she falls back on, and the security check's instruction.

The persona is text files under persona/, read in a set order. Her fixed lines
are in persona/lines.yaml. None of it is written in code.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError

FILES = ("SOUL.md", "VOICE.md", "POLICY.md", "RULES.md")
CHECK_FILE = "SECURITY_CHECK.md"
DESCRIBE_FILE = "IMAGE_DESCRIPTION.md"
STATUS_FILE = "status.yaml"
TIMES_OF_DAY = ("morning", "afternoon", "evening", "night")
STATUS_CHARS = 128  # what Discord allows in a status
# failure: the model cannot answer.  cutoff: ends a reply cut for length.
# refusal: replaces a reply holding a secret, path or private address.
# brush_off: replaces a reply that talks about her machinery.
# error: replaces a reply holding technical error text.
# image_failed: said when an image she was asked for could not be posted.
LINES = ("failure", "cutoff", "refusal", "brush_off", "error", "image_failed")


@dataclass(frozen=True)
class Persona:
    text: str
    lines: dict[str, tuple[str, ...]]  # each fixed line has one or more wordings
    security_check: str
    image_description: str = ""  # the instruction for describing an overheard picture
    statuses: dict[str, tuple[str, ...]] = field(default_factory=dict)  # time of day -> lines for under her name

    def line(self, name: str, choose: Any = random.choice) -> str:
        """One wording of a fixed line, picked at random when there are several."""
        return choose(self.lines[name])


def _wordings(name: str, value: Any) -> tuple[str, ...]:
    wordings = [value] if isinstance(value, str) else value
    if (not isinstance(wordings, list) or not wordings
            or not all(isinstance(w, str) and w.strip() and "\n" not in w.strip() for w in wordings)):
        raise ConfigError(f"persona line {name!r} must be one line of text, or a list of them")
    return tuple(w.strip() for w in wordings)


def load_persona(folder: Path) -> Persona:
    folder = Path(folder)
    parts = []
    for name in FILES:
        try:
            part = (folder / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if part:
            parts.append(part)
    if not parts:
        raise ConfigError(f"no persona files in {folder} (expected one or more of {', '.join(FILES)})")
    try:
        lines = yaml.safe_load((folder / "lines.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {folder / 'lines.yaml'}") from exc
    if not isinstance(lines, dict) or set(lines) != set(LINES):
        raise ConfigError(f"{folder / 'lines.yaml'} must hold exactly these lines: {', '.join(LINES)}")
    instructions = []
    for name in (CHECK_FILE, DESCRIBE_FILE):
        try:
            text = (folder / name).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"cannot read {folder / name}") from exc
        if not text:
            raise ConfigError(f"{folder / name} is empty")
        instructions.append(text)
    return Persona("\n\n".join(parts), {name: _wordings(name, lines[name]) for name in LINES}, *instructions,
                   _statuses(folder / STATUS_FILE))


def _statuses(path: Path) -> dict[str, tuple[str, ...]]:
    """The status lines by time of day. The file is optional: without it she shows no status."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"cannot read {path}") from exc
    if not isinstance(data, dict) or not set(data) <= set(TIMES_OF_DAY):
        raise ConfigError(f"{path} must be lists of lines under: {', '.join(TIMES_OF_DAY)}")
    statuses = {name: _wordings(name, lines) for name, lines in data.items()}
    for name, lines in statuses.items():
        if any(len(line) > STATUS_CHARS for line in lines):
            raise ConfigError(f"a status under {name!r} is longer than {STATUS_CHARS} characters")
    return statuses


def time_of_day(hour: int) -> str:
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    return "evening" if 18 <= hour < 23 else "night"
