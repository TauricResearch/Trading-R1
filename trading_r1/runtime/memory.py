# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""User memory: durable notes about the user that carry across chat sessions.

What the user has said about themselves and how they want the work done: who they are and
what they cover (``user``), how to work (``feedback``), what they are working on or tracking
(``project``), where to look (``reference``). The agent saves a note during a chat with the
``remember`` tool when the user states or confirms something durable, and every later chat
turn starts with the notes in its system prompt.

One Markdown file per note with YAML frontmatter. The directory is the index::

    ---
    name: covers-ig-credit
    type: user
    description: Covers investment-grade credit only
    updated: 2026-09-25
    ---
    The user covers IG credit (no high yield) and wants no buy/sell calls.

Nothing is overwritten unasked: ``remember`` rewrites a note only when given its name; a new
note that looks like a restatement is saved and flagged, for the model to reconcile.

Memory is a chat-session feature: runs outside a session never read or write it. Writes are atomic;
reads fail open: a malformed file is skipped with a warning, never raised.

Location: ``$TR1_MEMORY_DIR`` or ``~/.trading-r1/memory``.
"""

from __future__ import annotations

import datetime
import itertools
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from trading_r1.runtime.frontmatter import NAME_RE, FrontmatterError, split_frontmatter
from trading_r1.state.fileio import atomic_write_text
from trading_r1.state.paths import state_dir

_LOG = logging.getLogger(__name__)

TYPES = ("user", "feedback", "project", "reference")
BLOCK_CHARS = 8000  # cap on the injected notes (~2k tokens)
# Description-token overlap above which a new note is flagged as a likely restatement of an
# existing one (flagged, never merged: the model decides whether to update or drop one).
_DEDUP_JACCARD = 0.5


def default_memory_dir() -> Path:
    """Default memory directory, overridable with ``TR1_MEMORY_DIR``."""
    env = os.environ.get("TR1_MEMORY_DIR")
    if env:
        return Path(env).expanduser()
    return state_dir() / "memory"


def slug(text: str, max_chars: int = 48) -> str:
    """A kebab-case note name from free text, cut at a word boundary (``note`` if nothing
    usable is left, e.g. text with no ASCII letters)."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > max_chars:
        s = s[: max_chars + 1].rsplit("-", 1)[0] if "-" in s[:max_chars] else s[:max_chars]
    s = s if s[:1].isalpha() else f"m-{s}".rstrip("-")
    return s if NAME_RE.match(s) else "note"


@dataclass(frozen=True)
class Memory:
    """One note. Its ``name`` is its file's stem: the note's identity."""

    name: str
    type: str
    description: str
    body: str
    updated: str  # ISO date of the last write

    def render(self) -> str:
        fm = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "updated": self.updated,
        }
        head = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=1000)
        return f"---\n{head}---\n{self.body.strip()}\n"


def parse(text: str, name: str) -> Memory:
    """Parse the note stored under ``name``. Raises FrontmatterError on a malformed one."""
    fm, body = split_frontmatter(text, source=name)
    mtype, desc = fm.get("type"), fm.get("description")
    if mtype not in TYPES:
        raise FrontmatterError(f"memory {name} `type` must be one of {', '.join(TYPES)}")
    if not isinstance(desc, str) or not desc.strip() or not body:
        raise FrontmatterError(f"memory {name} needs a `description` and a body")
    # YAML reads an unquoted ISO date as a date; keep it a string.
    return Memory(name, mtype, desc.strip(), body, str(fm.get("updated") or ""))


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) > 2}


def _similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= _DEDUP_JACCARD


class MemoryStore:
    """File-backed notes. ``directory`` defaults to the user memory directory.

    Concurrent writers are safe per file (atomic replace); across processes the last write to
    a note wins."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else default_memory_dir()

    def _path(self, name: str) -> Path:
        return self.directory / f"{name}.md"

    def all(self) -> list[Memory]:
        """Every readable note, ordered by type then name."""
        out: list[Memory] = []
        for p in sorted(self.directory.glob("*.md")):
            try:
                if not NAME_RE.match(p.stem):
                    raise FrontmatterError("file name is not a kebab-case note name")
                out.append(parse(p.read_text(encoding="utf-8"), p.stem))
            except (OSError, UnicodeError, FrontmatterError) as err:
                _LOG.warning("skipping memory file %s: %s", p, err)
        return sorted(out, key=lambda m: (TYPES.index(m.type), m.name))

    def get(self, name: str) -> Memory | None:
        return next((m for m in self.all() if m.name == name), None)

    def similar(self, description: str, *, exclude: str | None = None) -> list[Memory]:
        """Notes whose description overlaps ``description``: likely restatements."""
        return [m for m in self.all() if m.name != exclude and _similar(m.description, description)]

    def save(self, mtype: str, description: str, body: str, name: str | None = None) -> Memory:
        """Write a note. ``name`` rewrites that note; without it the note is new, under a name
        of its own (never over an existing note)."""
        if name is None:
            base = slug(description)
            taken = {p.stem for p in self.directory.glob("*.md")}
            name = next(
                n
                for i in itertools.count(1)
                if (n := base if i == 1 else f"{base}-{i}") not in taken
            )
        elif not NAME_RE.match(name):
            raise ValueError(f"memory name {name!r} must be kebab-case")
        mem = Memory(
            name=name,
            type=mtype,
            description=" ".join(description.split()),
            body=body.strip(),
            updated=datetime.date.today().isoformat(),
        )
        atomic_write_text(self._path(name), mem.render())
        return mem

    def delete(self, name: str) -> bool:
        """Remove a note; False if there is none by that name."""
        if not NAME_RE.match(name):
            return False
        try:
            self._path(name).unlink()
            return True
        except FileNotFoundError:
            return False

    def block(self, max_chars: int = BLOCK_CHARS, *, editable: bool = True) -> str:
        """The notes as a system-prompt section (saying how to change them when the run can),
        whole notes within ``max_chars`` (in type
        order, so reference notes are the first left out); empty when there are none."""
        notes = self.all()
        if not notes:
            return ""
        lines = [
            "## What you remember about the user",
            "Notes from earlier sessions (`remember`): what the user said about themselves, "
            "how they want the work done, and what they are working on. Work within them. They "
            "are context, not the question."
            + (
                " When the user says something new or different, update the note (`remember` "
                "with its name) or drop it (`forget`)."
                if editable
                else ""
            ),
            "",
        ]
        room = max_chars - sum(len(x) + 1 for x in lines) - len(_MORE.format(n=len(notes)))
        left_out = 0
        for m in notes:
            line = f"- `{m.name}` ({m.type}, {m.updated}): **{m.description}**: {m.body}"
            if len(line) + 1 > room:
                left_out += 1
                continue
            lines.append(line)
            room -= len(line) + 1
        if left_out:
            lines.append(_MORE.format(n=left_out))
        return "\n".join(lines)


_MORE = "- … {n} more notes not shown."
