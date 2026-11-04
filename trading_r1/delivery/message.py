# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""What is sent: a report as plain text, with its files attached.

A link survives only if the report cites that page (its structured sources); any other address a
model wrote into its answer is dropped. A message made from a report keeps its parts
(``ReportText``) for a channel that formats them itself.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from trading_r1.report import PdfTextError, render_html, render_pdf
from trading_r1.report.common import cited_urls
from trading_r1.report.snapshot import DISCLAIMER, ReportSnapshot
from trading_r1.state.runtime_config import runtime_config

_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)")
_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_BARE_URL = re.compile(r"(?:https?://|www\.)[^\s<>)\]]*[^\s<>)\].,;:!?'\"]", re.IGNORECASE)
MAX_SOURCES = 5  # sources listed in the body; the rest are in the attached report


@dataclass(frozen=True)
class Attachment:
    name: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class ReportText:
    """What a report says, before any channel formats it."""

    answer: str  # markdown
    allowed: frozenset[str]  # the addresses a link may keep
    sources: tuple[tuple[int, str, str], ...]  # (number, plain title, address or "") shown
    more: int  # sources left out of ``sources``
    footer: str  # the disclaimer
    attached: bool = True  # a file goes with the message

    @property
    def more_note(self) -> str:
        where = " in the attached report" if self.attached else ""
        return f"\n(+{self.more} more{where})" if self.more else ""


@dataclass(frozen=True)
class Message:
    subject: str
    text: str
    attachments: tuple[Attachment, ...] = field(default=())
    name: str = ""  # what names a saved copy: a session id or a job's name
    report: ReportText | None = None  # set when the message is a report

    def __post_init__(self) -> None:
        names = [a.name for a in self.attachments]
        if len(set(names)) != len(names) or any(n != Path(n).name or not n for n in names):
            raise ValueError("attachments are named by distinct plain file names")

    def sendable(self) -> tuple[Attachment, ...]:
        """What goes with a mailbox or chat message: the PDF (it opens on a phone), else the page,
        else the markdown, when it is small enough."""
        for kind in ("application/pdf", "text/html", "text/markdown"):
            found = tuple(
                a
                for a in self.attachments
                if a.content_type == kind
                and len(a.data) <= runtime_config().delivery.max_attachment_bytes
            )
            if found:
                return found
        return ()


def plain_text(markdown: str, allowed: frozenset[str]) -> str:
    """Markdown as plain text: headings and bold lose their marks, images become their alt text,
    a link keeps its address only when it is in ``allowed``, and a bare address that is not is
    removed."""
    text = _HEADING.sub("", _EMPHASIS.sub(r"\2", _IMAGE.sub(lambda m: m.group(1), markdown)))
    text = _LINK.sub(
        lambda m: f"{m.group(1)} ({m.group(2)})" if m.group(2) in allowed else m.group(1), text
    )
    return _BARE_URL.sub(lambda m: m.group(0) if m.group(0) in allowed else "[link removed]", text)


def _headline(snapshot: ReportSnapshot, allowed: frozenset[str]) -> str:
    """The answer's heading, else its first line of text: what a reader sees in an inbox list."""
    lines = [ln.strip() for ln in snapshot.answer.splitlines() if ln.strip()]
    line = next(
        (ln for ln in lines if ln.startswith("#")), lines[0] if lines else snapshot.question
    )
    text = " ".join(plain_text(line.lstrip("#>*- ").replace("**", ""), allowed).split())
    return text if len(text) <= 80 else text[:79].rsplit(" ", 1)[0] + "…"


def _subject(snapshot: ReportSnapshot, label: str, allowed: frozenset[str]) -> str:
    """``<job or Trading-R1> <date>: <headline>``: a mailbox can be filtered by its label."""
    head = " ".join(p for p in (label, snapshot.created[:10]) if p)
    return f"{head}: {_headline(snapshot, allowed)}"[:160]


REPORT_HTML = "trading-r1-report.html"
REPORT_PDF = "trading-r1-report.pdf"
REPORT_MD = "trading-r1-report.md"


def _page(snapshot: ReportSnapshot) -> tuple[Attachment, ...]:
    """The HTML page as a file; none when it cannot be built, since the text still goes."""
    try:
        page = render_html(snapshot, links="cited").encode("utf-8")
    except Exception:  # noqa: BLE001 (a rendering failure must not stop a delivery)
        logging.getLogger(__name__).exception("the page could not be built")
        return ()
    return (Attachment(REPORT_HTML, "text/html", page),)


def _files(snapshot: ReportSnapshot, formats: tuple[str, ...]) -> tuple[Attachment, ...]:
    """The chosen files. A PDF that cannot be laid out is replaced by the page, so a chat or a
    mailbox still gets the report as a file."""
    files: list[Attachment] = []
    for fmt in formats:
        if fmt == "pdf":
            try:
                pdf = render_pdf(snapshot, links="cited")
            except PdfTextError as err:  # expected for some reports: the page takes its place
                logging.getLogger(__name__).info("no PDF: %s", err)
                if "html" not in formats:
                    files.extend(_page(snapshot))
                continue
            except Exception:  # noqa: BLE001 (a layout failure must not stop a delivery)
                logging.getLogger(__name__).exception("the PDF could not be built")
                if "html" not in formats:
                    files.extend(_page(snapshot))
                continue
            files.append(Attachment(REPORT_PDF, "application/pdf", pdf))
        elif fmt == "markdown":
            files.append(Attachment(REPORT_MD, "text/markdown", snapshot.markdown().encode()))
        elif fmt == "html":
            files.extend(_page(snapshot))
    return tuple(files)


def report_message(
    snapshot: ReportSnapshot,
    name: str = "",
    job: bool = False,
    formats: tuple[str, ...] = ("pdf", "markdown"),
) -> Message:
    """The report as a message: its answer and sources as plain text, and its files in the chosen
    ``formats`` (pdf, markdown, html) attached. A mailbox or a chat takes one (``sendable``: the
    PDF, else the page, else the markdown), a folder all of them. ``name`` is the session id or
    the job's name, and a ``job``'s name leads the subject."""
    allowed = cited_urls(snapshot)
    files = _files(snapshot, formats)
    listed = MAX_SOURCES if formats else len(snapshot.sources)  # with no file, the list is all
    sources = tuple(
        (x.index, plain_text(x.title, allowed), x.url if x.url in allowed else "")
        for x in snapshot.sources[:listed]
    )
    footer = DISCLAIMER if formats else ""  # a report carries it; a reply in a chat does not
    if "pdf" in formats and not any(a.name == REPORT_PDF for a in files):
        footer = f"This report could not be made as a PDF. {DISCLAIMER}"
    # a reply in a chat (no files) has no subject line: it is a message, not a mailing
    subject = _subject(snapshot, name if job else "Trading-R1", allowed) if formats else ""
    message = Message(subject, "", files, name)
    report = ReportText(
        snapshot.answer,
        allowed,
        sources,
        max(len(snapshot.sources) - listed, 0),
        footer,
        attached=bool(message.sendable()),
    )
    body = plain_text(snapshot.answer, allowed).strip()
    if sources:
        lines = "\n".join(f"{i}. {t}" + (f" ({u})" if u else "") for i, t, u in sources)
        body += f"\n\nSources\n{lines}{report.more_note}"
    return replace(message, text=f"{body}\n\n{footer}".rstrip(), report=report)


def failure_message(job: str, reason: str) -> Message:
    """A short notice that a scheduled run produced no report."""
    return Message(
        f"{job}: did not complete",
        f"{job!r} produced no report ({reason}). It was not retried.",
        name=job,
    )
