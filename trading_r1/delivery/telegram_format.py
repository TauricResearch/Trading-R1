# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""A report's markdown as Telegram's small HTML subset, in blocks that each stand alone.

Telegram renders bold, italic, strikethrough, code, preformatted text, quotes and links; it has no
headings, lists or tables, so a heading becomes a bold line, a list bullets, and a table lines of
cells joined by slashes. A link survives only if it is in ``allowed`` (the pages the report cites);
any other address is dropped, as in the plain-text body.
"""

from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token

from trading_r1.delivery.message import Message

_BARE_URL = re.compile(r"(?:https?://|www\.)[^\s<>)\]]*[^\s<>)\].,;:!?'\"]", re.IGNORECASE)
_TAGS = {
    "strong_open": "<b>",
    "strong_close": "</b>",
    "em_open": "<i>",
    "em_close": "</i>",
    "s_open": "<s>",
    "s_close": "</s>",
}


def _md() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    return md.enable(["table", "strikethrough"])


def _text(content: str, allowed: frozenset[str]) -> str:
    kept = _BARE_URL.sub(
        lambda m: m.group(0) if m.group(0) in allowed else "[link removed]", content
    )
    return html.escape(kept, quote=False)


def _inline(token: Token, allowed: frozenset[str]) -> str:
    out: list[str] = []
    open_link: list[bool] = []
    for c in token.children or ():
        if c.type in _TAGS:
            out.append(_TAGS[c.type])
        elif c.type == "text":
            out.append(_text(c.content, allowed))
        elif c.type == "code_inline":
            out.append(f"<code>{html.escape(c.content, quote=False)}</code>")
        elif c.type in ("softbreak", "hardbreak"):
            out.append("\n")
        elif c.type == "image":
            out.append(html.escape(c.content, quote=False))
        elif c.type == "link_open":
            href = str(c.attrGet("href") or "")
            open_link.append(href in allowed)
            if open_link[-1]:
                out.append(f'<a href="{html.escape(href, quote=True)}">')
        elif c.type == "link_close" and open_link and open_link.pop():
            out.append("</a>")
    return "".join(out)


def _end(tokens: list[Token], start: int) -> int:
    """The index of the token that closes the one at ``start``."""
    depth = 0
    for i in range(start, len(tokens)):
        depth += tokens[i].nesting
        if depth == 0:
            return i
    return len(tokens) - 1


def _list(tokens: list[Token], start: int, allowed: frozenset[str], depth: int) -> list[str]:
    end = _end(tokens, start)
    ordered = tokens[start].type == "ordered_list_open"
    lines: list[str] = []
    number = int(tokens[start].attrGet("start") or 1) if ordered else 0
    i = start + 1
    while i < end:
        if tokens[i].type == "list_item_open":
            stop = _end(tokens, i)
            mark = f"{number}." if ordered else "•"
            number += 1
            first = True
            j = i + 1
            while j < stop:
                t = tokens[j]
                if t.type == "inline":
                    text = _inline(t, allowed)
                    lines.append("  " * depth + (f"{mark} " if first else "  ") + text)
                    first = False
                elif t.type in ("bullet_list_open", "ordered_list_open"):
                    lines.extend(_list(tokens, j, allowed, depth + 1))
                    j = _end(tokens, j)
                j += 1
            i = stop
        i += 1
    return lines


def _table(tokens: list[Token], start: int, allowed: frozenset[str]) -> str:
    end = _end(tokens, start)
    rows: list[list[str]] = []
    for i in range(start, end):
        if tokens[i].type == "tr_open":
            rows.append([])
        elif tokens[i].type == "inline":
            rows[-1].append(_inline(tokens[i], allowed))
    lines = [" / ".join(r) for r in rows]
    if lines:
        lines[0] = f"<b>{lines[0]}</b>"
    return "\n".join(lines)


def _blocks(tokens: list[Token], allowed: frozenset[str]) -> list[str]:
    blocks: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "heading_open":
            blocks.append(f"<b>{_inline(tokens[i + 1], allowed)}</b>")
        elif t.type == "paragraph_open":
            blocks.append(_inline(tokens[i + 1], allowed))
        elif t.type in ("fence", "code_block"):
            blocks.append(f"<pre>{html.escape(t.content.rstrip(), quote=False)}</pre>")
        elif t.type in ("bullet_list_open", "ordered_list_open"):
            blocks.append("\n".join(_list(tokens, i, allowed, 0)))
            i = _end(tokens, i)
        elif t.type == "table_open":
            blocks.append(_table(tokens, i, allowed))
            i = _end(tokens, i)
        elif t.type == "blockquote_open":
            end = _end(tokens, i)
            inner = "\n\n".join(_blocks(tokens[i + 1 : end], allowed))
            blocks.append(f"<blockquote>{inner}</blockquote>")
            i = end
        i += 1
        if t.type in ("heading_open", "paragraph_open"):
            i = _end(tokens, i - 1) + 1
    return [b for b in blocks if b.strip()]


def telegram_blocks(markdown: str, allowed: frozenset[str]) -> list[str]:
    """The markdown as Telegram HTML blocks, each valid on its own."""
    return _blocks(_md().parse(markdown), allowed)


def report_blocks(message: Message) -> tuple[str, ...]:
    """A report message as Telegram HTML blocks: the subject, the answer (without its own title
    line when a subject carries it), the sources and the notice. A message that is not a report
    gives none."""
    report = message.report
    if report is None:
        return ()
    blocks = telegram_blocks(report.answer, report.allowed)
    if message.subject and report.answer.lstrip().startswith("# "):
        blocks = blocks[1:]
    out = [f"<b>{html.escape(message.subject, quote=False)}</b>"] if message.subject else []
    out += blocks
    if report.sources:
        rows = "\n".join(
            f"{i}. "
            + (
                f'<a href="{html.escape(u)}">{html.escape(t or u, quote=False)}</a>'
                if u
                else html.escape(t, quote=False)
            )
            for i, t, u in report.sources
        )
        out.append(f"<b>Sources</b>\n{rows}{report.more_note}")
    if report.footer:
        out.append(f"<i>{html.escape(report.footer, quote=False)}</i>")
    return tuple(out)
