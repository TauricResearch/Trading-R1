# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Citation post-processing: pure end-of-run validation + bibliography.

Applied at the single exit (`Agent._build_result` via the `finalize` hook):
strip any model-written References list (the harness owns it), drop orphan `[N]` markers (numbers
outside the conversation's sources), and append the `## References` list of the cited sources,
under the numbers they have for the whole conversation (read but uncited ones stay in the rail
and the trajectory). When the cache is empty, no block is
appended.

Pure functions over the citation list; no I/O.
"""

from __future__ import annotations

import re
from typing import Any

# A model-written reference section (an h1-h6 heading, bold, or bare line) that is only the word
# "References": a heading that goes on ("References methodology") is report text, kept.
_REF_SECTION_RE = re.compile(
    r"\n+(?:#{1,6}\s*references:?[ \t]*(?=\n|$)|\*\*\s*references:?\s*\*\*[ \t]*(?=\n|$)"
    r"|references:?[ \t]*\n)",
    flags=re.IGNORECASE,
)
# What marks a paragraph as a list of references: an item label, a link or an address.
_REF_LINE_RE = re.compile(
    r"^[ \t]*(?:\*\*)?(?:\[\d+\]|\d+[.)]|[-*][ \t])|https?://|\]\(", flags=re.MULTILINE
)
_HEADING_RE = re.compile(r"^(#{1,6})\s", flags=re.MULTILINE)
_PARAGRAPH_RE = re.compile(r"\S(?:.|\n(?![ \t]*\n))*")
# A code span (kept as written), or a `[N]` / `[N, M]` marker that is not a link's text; a longer
# number in brackets, such as `[2023]`, is the writer's text.
_BRACKET_RE = re.compile(
    r"(?P<code>`[^`\n]*`)"
    # numbers, perhaps with a note on the data beside them (`[13, data: fundamentals]`), or the note
    r"|(?P<lead>\s*)\[(?:(?P<digits>\d{1,3}(?:\s*,\s*\d{1,3})*)(?:\s*[,;]\s*)?)?"
    r"(?:data:[^\]\n]*)?\](?!\()"
)


def format_references_block(citations: list[Any]) -> str:
    """Render the bibliography: ``[N] title`` + ``    url`` per source."""
    lines: list[str] = []
    for c in citations:
        title = getattr(c, "title", None) or getattr(c, "url", "") or "(untitled)"
        url = getattr(c, "url", "")
        lines.append(f"[{c.index}] {title}")
        if url:
            lines.append(f"    {url}")
    return "\n".join(lines)


def _link_text(title: str) -> str:
    return title.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def format_bibliography(entries: list[Any]) -> str:
    """The References list: one item per cited source, ``- [N] [title](url)``, under its number
    (a numbered markdown list would renumber one with gaps)."""
    lines = []
    for c in entries:
        title = getattr(c, "title", None) or getattr(c, "url", "") or "(untitled)"
        url = getattr(c, "url", "")
        lines.append(
            f"- [{c.index}] [{_link_text(title)}]({url})" if url else f"- [{c.index}] {title}"
        )
    return "\n".join(lines)


def _end_of_list(section: str) -> int | None:
    """Where a section's list of references ends: an intro or subheadings may lead or divide it,
    and the first prose after it stops it. None when it has no list."""
    end = None
    for paragraph in _PARAGRAPH_RE.finditer(section):
        if _REF_LINE_RE.search(paragraph.group()):
            end = paragraph.end()
        elif end is not None and not _HEADING_RE.match(paragraph.group()):
            break
    return end


def _without_reference_lists(text: str) -> str:
    """``text`` without a model-written References heading and the list under it. A section is
    its heading's lines up to the next heading of its level or above; prose after the list is the
    writer's, and so is a section that is only prose."""
    kept: list[str] = []
    at = 0
    for m in _REF_SECTION_RE.finditer(text):
        if m.start() < at:
            continue
        level = len(h.group(1)) if (h := _HEADING_RE.search(m.group().lstrip("\n"))) else 7
        end = next(
            (h.start() for h in _HEADING_RE.finditer(text, m.end()) if len(h.group(1)) <= level),
            len(text),
        )
        section = text[m.end() : end]
        listed = _end_of_list(section)
        if section.strip() and listed is None:
            continue
        kept.append(text[at : m.start()].strip())
        at = m.end() + (listed if listed is not None else len(section))
    kept.append(text[at:].lstrip())
    return "\n\n".join(part for part in kept if part)


def validate_and_append_references(text: str, citations: list[Any]) -> tuple[str, dict[str, Any]]:
    """Validate the inline markers and append the matching ``## References`` list, in the order
    of the sources' numbers. Markers outside the sources and model-written reference sections
    are dropped. Returns ``(text, stats)``."""
    body = _without_reference_lists(text).rstrip()
    by_index = {c.index: c for c in citations}
    cited: set[int] = set()
    orphans: set[int] = set()

    def _check(match: re.Match[str]) -> str:
        if match.group("code"):
            return match.group("code")
        digits = match.group("digits")
        if digits is None and match.group(0).strip() == "[]":  # empty brackets are the writer's
            return match.group(0)
        nums = [int(d) for d in (digits or "").replace(" ", "").split(",") if d]
        kept = [n for n in nums if n in by_index]
        cited.update(kept)
        orphans.update(n for n in nums if n not in by_index)
        if not kept:
            return ""
        return f"{match.group('lead')}[{', '.join(map(str, sorted(set(kept))))}]"

    body = _BRACKET_RE.sub(_check, body)
    stats = {
        "n_refs": len(citations),
        "n_inline_citations": len(cited),
        "cited_indices": sorted(cited),
        "n_orphans_stripped": len(orphans),
        "unused_indices": sorted(set(by_index) - cited),
    }
    if not cited:  # no sources, or sources read but none cited: no bibliography
        return body + "\n", stats
    entries = [by_index[i] for i in sorted(cited)]
    return body + "\n\n## References\n\n" + format_bibliography(entries) + "\n", stats
