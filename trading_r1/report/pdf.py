# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The report as a PDF: the same content as the page, laid out for paper and for a phone's reader.

Built with ReportLab from the answer's markdown, in Open Sans (embedded). No browser or system
library is involved. The logo is drawn as vectors from
``logo.svg``. Link rules are the page's: only http(s) targets, and with ``links="cited"`` only
pages the report cites. Open Sans has the Latin, Greek and Cyrillic alphabets: a report with letters
or figures outside them raises ``PdfTextError``, since they could not be drawn. Other symbols it
lacks are left out.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

from markdown_it.token import Token
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from svgelements import SVG, Arc, Close, CubicBezier, Line, Move, QuadraticBezier
from svgelements import Path as SvgPath
from svgelements import Rect as SvgRect

from trading_r1.report.common import (
    MARKER,
    link_allowed,
    link_scope,
    markdown,
    when,
)
from trading_r1.report.snapshot import DATA_EARLIER, DISCLAIMER, ReportSnapshot

_HERE = Path(__file__).parent
_INK, _MUTED, _RULE, _WASH = (HexColor(c) for c in ("#1d1d1b", "#6a6a64", "#dcdad2", "#f1efe8"))
_ACCENT, _BRAND, _BRANDINK = HexColor("#1c5d99"), HexColor("#14c290"), HexColor("#0b8a65")
_NUMBER = re.compile(r"^[+\-−(]?[$€£¥]?[\d][\d,.]*\s?(?:%|x|[kKMBT]|bp|bps)?\)?$")
_LINKS = (
    ("Tauric Research", "https://tauric.ai"),
    ("GitHub", "https://github.com/TauricResearch/Trading-R1"),
)
_MARGIN = 64  # points; about 90 characters to a line
_FONTS = (
    ("OpenSans", "OpenSans-Regular.ttf"),
    ("OpenSans-Bold", "OpenSans-Bold.ttf"),
    ("OpenSans-Italic", "OpenSans-Italic.ttf"),
    ("OpenSans-BoldItalic", "OpenSans-BoldItalic.ttf"),
)


def _register_fonts() -> None:
    if "OpenSans" in pdfmetrics.getRegisteredFontNames():
        return
    for name, file in _FONTS:
        pdfmetrics.registerFont(TTFont(name, str(_HERE / "fonts" / file)))
    pdfmetrics.registerFontFamily(
        "OpenSans",
        normal="OpenSans",
        bold="OpenSans-Bold",
        italic="OpenSans-Italic",
        boldItalic="OpenSans-BoldItalic",
    )


def _styles() -> dict[str, ParagraphStyle]:
    base = {"fontName": "OpenSans", "textColor": _INK, "fontSize": 10, "leading": 15}

    def make(name: str, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, **{**base, **kw})

    return {
        "body": make("body", spaceAfter=7),
        "h1": make("h1", fontName="OpenSans-Bold", fontSize=20, leading=25, spaceBefore=4,
                   spaceAfter=8, keepWithNext=1),
        "h2": make("h2", fontName="OpenSans-Bold", fontSize=13.5, leading=18, spaceBefore=3,
                   spaceAfter=5, keepWithNext=1),
        "h3": make("h3", fontName="OpenSans-Bold", fontSize=11, leading=15, spaceBefore=10,
                   spaceAfter=3, keepWithNext=1),
        "kicker": make("kicker", fontName="OpenSans-Bold", fontSize=7.5, leading=10,
                       textColor=_BRANDINK, spaceAfter=3),
        "question": make("question", fontName="OpenSans-Italic", fontSize=11, leading=15,
                         textColor=_MUTED, spaceAfter=6),
        "meta": make("meta", fontSize=8, leading=11.5, textColor=_MUTED),
        "quote": make("quote", leftIndent=14, textColor=_MUTED, fontName="OpenSans-Italic"),
        "cell": make("cell", fontSize=8.5, leading=11.5, spaceAfter=0),
        "cellhead": make("cellhead", fontName="OpenSans-Bold", fontSize=8, leading=11,
                         textColor=_MUTED, spaceAfter=0),
        "source": make("source", fontSize=8.5, leading=12, leftIndent=18, spaceAfter=2),
        "small": make("small", fontSize=8, leading=11.5, textColor=_MUTED),
        "code": ParagraphStyle("code", fontName="Courier", fontSize=8.5, leading=12,
                               textColor=_INK, backColor=_WASH, borderPadding=5),
    }  # fmt: skip


class _Logo(Flowable):
    """``logo.svg`` drawn as vectors at a given height."""

    def __init__(self, height: float) -> None:
        super().__init__()
        svg = SVG.parse(str(_HERE / "logo.svg"))
        self._items = list(svg.elements())
        w, h = (
            svg.viewbox.width,
            svg.viewbox.height,
        )  # the parsed elements sit at the viewbox origin
        self.height, self._scale = height, height / h
        self.width = w * self._scale

    def wrap(self, *_args) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        c.saveState()
        c.translate(0, self.height)
        c.scale(self._scale, -self._scale)
        for e in self._items:
            if isinstance(e, SvgRect) and e.fill is not None and e.fill.value is not None:
                c.setFillColor(HexColor(e.fill.hexrgb))
                c.roundRect(e.x, e.y, e.width, e.height, e.rx or 0, stroke=0, fill=1)
            elif isinstance(e, SvgPath):
                self._path(c, e)
        c.restoreState()

    @staticmethod
    def _path(c, e) -> None:
        p = c.beginPath()
        for seg in e.segments():
            if isinstance(seg, Move):
                p.moveTo(seg.end.x, seg.end.y)
            elif isinstance(seg, (Line, Close)):
                p.lineTo(seg.end.x, seg.end.y) if isinstance(seg, Line) else p.close()
            elif isinstance(seg, CubicBezier):
                p.curveTo(seg.control1.x, seg.control1.y, seg.control2.x, seg.control2.y,
                          seg.end.x, seg.end.y)  # fmt: skip
            elif isinstance(seg, (QuadraticBezier, Arc)):
                for cub in seg.as_cubic_curves() if isinstance(seg, Arc) else [seg]:
                    cub = cub if isinstance(cub, CubicBezier) else _cubic(cub)
                    p.curveTo(cub.control1.x, cub.control1.y, cub.control2.x, cub.control2.y,
                              cub.end.x, cub.end.y)  # fmt: skip
        stroke = e.stroke is not None and e.stroke.value is not None
        fill = e.fill is not None and e.fill.value is not None
        if stroke:
            c.setStrokeColor(HexColor(e.stroke.hexrgb))
            c.setLineWidth(e.stroke_width or 1)
            c.setLineCap(1)
            c.setLineJoin(1)
        if fill:
            c.setFillColor(HexColor(e.fill.hexrgb))
        c.drawPath(p, stroke=int(stroke), fill=int(fill))


def _cubic(q: QuadraticBezier) -> CubicBezier:
    s, k, e = q.start, q.control, q.end
    return CubicBezier(
        s, s + (k - s) * (2 / 3), e + (k - e) * (2 / 3), e
    )  # a quadratic as the cubic with the same curve


class _Builder:
    """Markdown tokens to flowables."""

    def __init__(self, known: set[int], cited: frozenset[str] | None) -> None:
        self.known, self.cited, self.s = known, cited, _styles()

    def _text(self, content: str, in_link: bool) -> str:
        out, pos = [], 0
        for m in MARKER.finditer(content):
            if in_link or int(m.group(1)) not in self.known:
                continue
            out.append(escape(content[pos : m.start()]))
            out.append(f'<a href="#src-{m.group(1)}" color="#1c5d99">{m.group(0)}</a>')
            pos = m.end()
        return "".join(out) + escape(content[pos:])

    def inline(self, token: Token) -> str:
        tags = {"strong_open": "<b>", "strong_close": "</b>", "em_open": "<i>", "em_close": "</i>",
                "s_open": "<strike>", "s_close": "</strike>"}  # fmt: skip
        out: list[str] = []
        links: list[bool] = []
        for c in token.children or ():
            if c.type in tags:
                out.append(tags[c.type])
            elif c.type == "text":
                out.append(self._text(c.content, bool(links)))
            elif c.type == "code_inline":
                out.append(f'<font face="Courier" size="9">{escape(c.content)}</font>')
            elif c.type in ("softbreak", "hardbreak"):
                out.append("<br/>" if c.type == "hardbreak" else " ")
            elif c.type == "link_open":
                href = str(c.attrGet("href") or "")
                ok = href.startswith("#") or link_allowed(href, self.cited)
                links.append(ok)
                if ok:
                    out.append(f'<a href="{escape(href, {chr(34): "&quot;"})}" color="#1c5d99">')
            elif c.type == "link_close" and links and links.pop():
                out.append("</a>")
        return "".join(out)

    def blocks(self, tokens: list[Token], depth: int = 0) -> list[Flowable]:
        out: list[Flowable] = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            end = i + 2 if t.type in ("heading_open", "paragraph_open") else i
            if t.type == "heading_open":
                level = min(int(t.tag[1:]), 3)
                if level == 2:
                    out.append(_heading(self.inline(tokens[i + 1]), self.s)[0])
                out.append(Paragraph(self.inline(tokens[i + 1]), self.s[f"h{level}"]))
            elif t.type == "paragraph_open":
                style = self.s["quote"] if depth < 0 else self.s["body"]
                out.append(Paragraph(self.inline(tokens[i + 1]), style))
            elif t.type in ("fence", "code_block"):
                out.append(Preformatted(t.content.rstrip(), self.s["code"], maxLineLength=96))
                out.append(Spacer(1, 6))
            elif t.type in ("bullet_list_open", "ordered_list_open"):
                end = _end(tokens, i)
                out.extend(self._list(tokens[i : end + 1], depth))
            elif t.type == "table_open":
                end = _end(tokens, i)
                out.extend([self._table(tokens[i : end + 1]), Spacer(1, 8)])
            elif t.type == "blockquote_open":
                end = _end(tokens, i)
                out.extend(self.blocks(tokens[i + 1 : end], depth=-1))
            elif t.type == "hr":
                out.append(HRFlowable(width="100%", thickness=0.6, color=_RULE, spaceBefore=6,
                                      spaceAfter=6))  # fmt: skip
            i = end + 1
        return out

    def _list(self, tokens: list[Token], depth: int) -> list[Flowable]:
        ordered = tokens[0].type == "ordered_list_open"
        number = int(tokens[0].attrGet("start") or 1)
        out: list[Flowable] = []
        i = 1
        while i < len(tokens) - 1:
            if tokens[i].type == "list_item_open":
                stop = _end(tokens, i)
                mark, number = (f"{number}.", number + 1) if ordered else ("•", number)
                first, j = True, i + 1
                while j < stop:
                    t = tokens[j]
                    if t.type == "inline":
                        style = ParagraphStyle(
                            "li", parent=self.s["body"], leftIndent=16 + 14 * depth,
                            bulletIndent=4 + 14 * depth, spaceAfter=2,
                        )  # fmt: skip
                        out.append(
                            Paragraph(self.inline(t), style, bulletText=mark if first else "")
                        )
                        first = False
                    elif t.type in ("bullet_list_open", "ordered_list_open"):
                        end = _end(tokens, j)
                        out.extend(self._list(tokens[j : end + 1], depth + 1))
                        j = end
                    j += 1
                i = stop
            i += 1
        if depth == 0:
            out.append(Spacer(1, 4))
        return out

    def _table(self, tokens: list[Token]) -> Flowable:
        rows: list[list[Token]] = []
        heads: list[bool] = []
        for t in tokens:
            if t.type == "tr_open":
                rows.append([])
                heads.append(False)
            elif t.type == "th_open":
                heads[-1] = True
            elif t.type == "inline":
                rows[-1].append(t)
        width = LETTER[0] - 2 * _MARGIN
        plain = [[re.sub(r"<[^>]+>", "", self.inline(c)) for c in r] for r in rows]
        ncols = max(len(r) for r in rows)
        weight = [
            min(max([len(r[k]) for r in plain if k < len(r)] + [6]), 38) for k in range(ncols)
        ]
        cols = [width * w / sum(weight) for w in weight]
        body = [p for p, head in zip(plain, heads, strict=True) if not head]
        right = [
            bool(k)
            and any(k < len(r) and r[k].strip() for r in body)
            and all(_NUMBER.match(r[k].strip()) for r in body if k < len(r) and r[k].strip())
            for k in range(ncols)
        ]
        data = []
        for r, head in zip(rows, heads, strict=True):
            cells = []
            for k, c in enumerate(r):
                align = TA_RIGHT if right[k] else 0
                style = ParagraphStyle(
                    "c", parent=self.s["cellhead" if head else "cell"], alignment=align
                )
                cells.append(Paragraph(self.inline(c), style))
            data.append(cells + [""] * (ncols - len(cells)))
        table = Table(data, colWidths=cols, repeatRows=1 if heads and heads[0] else 0)
        table.setStyle(_TABLE)
        return table


_TABLE = TableStyle([
    ("LINEBELOW", (0, 0), (-1, -1), 0.4, _RULE),
    ("BACKGROUND", (0, 0), (-1, 0), _WASH),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
])  # fmt: skip


def _heading(text: str, s: dict[str, ParagraphStyle]) -> list[Flowable]:
    rule = HRFlowable(width="100%", thickness=1.4, color=_BRAND, spaceBefore=12, spaceAfter=5)
    rule.keepWithNext = 1
    return [rule, Paragraph(text, s["h2"])]


def _end(tokens: list[Token], start: int) -> int:
    depth = 0
    for i in range(start, len(tokens)):
        depth += tokens[i].nesting
        if depth == 0:
            return i
    return len(tokens) - 1


def _footer(canvas, doc) -> None:
    w = LETTER[0]
    canvas.saveState()
    canvas.setFont("OpenSans", 7.5)
    canvas.setFillColor(_MUTED)
    canvas.drawString(_MARGIN, 30, f"Trading-R1, page {doc.page}")
    x = w - _MARGIN
    canvas.setFont("OpenSans-Bold", 7.5)
    canvas.setFillColor(_BRANDINK)
    for name, url in reversed(_LINKS):
        width = pdfmetrics.stringWidth(name, "OpenSans-Bold", 7.5)
        canvas.drawString(x - width, 30, name)
        canvas.linkURL(url, (x - width, 27, x, 38), relative=0)
        x -= width + 14
    canvas.restoreState()


class PdfTextError(ValueError):
    """The report has text the PDF's font cannot draw."""


# Symbols the font lacks that carry meaning, as plain text.
_PLAIN = str.maketrans({"→": "->", "←": "<-", "↑": "up ", "↓": "down ", "▲": "up ", "▼": "down "})
# Letters, digits and currency signs: a page without them says something else.
_MUST_DRAW = ("L", "N", "Sc")


def _drawn_text(snap: ReportSnapshot) -> str:
    """The text the report draws: what the answer's markdown shows, not the addresses it links."""
    parts = [snap.question, snap.model, *(s.title or s.url for s in snap.sources)]
    parts += [field for row in snap.data for field in (row.call, row.source)]
    for token in markdown().parse(snap.answer):
        parts += [child.content for child in token.children or []]
        if token.type in ("fence", "code_block"):
            parts.append(token.content)
    return " ".join(parts)


def _check_text(snap: ReportSnapshot) -> None:
    """Refuse a report whose words or figures would come out blank."""
    drawn = pdfmetrics.getFont("OpenSans").face.charToGlyph
    missing = sorted(
        {
            c
            for c in _drawn_text(snap)
            if unicodedata.category(c).startswith(_MUST_DRAW) and ord(c) not in drawn
        }
    )
    if missing:
        raise PdfTextError(
            f"the PDF's font cannot draw this report's text ({''.join(missing[:5])}…)"
        )


def render_pdf(snap: ReportSnapshot, *, links: Literal["web", "cited"] = "web") -> bytes:
    """The report as a PDF document; ``links`` as for ``render_html``. Raises ``PdfTextError``
    when the font cannot draw its text."""
    _register_fonts()
    snap = replace(snap, answer=snap.answer.translate(_PLAIN))
    _check_text(snap)
    cited = link_scope(snap, links)
    b = _Builder({s.index for s in snap.sources}, cited)
    s = b.s
    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=LETTER, leftMargin=_MARGIN, rightMargin=_MARGIN, topMargin=_MARGIN,
        bottomMargin=_MARGIN + 6, title=snap.question[:120], author="Trading-R1",
    )  # fmt: skip
    frame = Frame(
        _MARGIN, _MARGIN + 6, LETTER[0] - 2 * _MARGIN, LETTER[1] - 2 * _MARGIN - 6, id="body",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )  # fmt: skip
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=_footer)])
    facts = " / ".join(
        f"{label} {value}"
        for label, value in (
            ("Model", snap.model),
            ("Mode", snap.mode),
            ("Date", when(snap.created)),
            ("Version", snap.version),
        )  # fmt: skip
        if value
    )
    story: list[Flowable] = [
        _Logo(30),
        Spacer(1, 14),
        Paragraph("RESEARCH REPORT", s["kicker"]),
        Paragraph(escape(snap.question), s["question"]),
        Paragraph(escape(facts), s["meta"]),
        HRFlowable(width="100%", thickness=0.6, color=_RULE, spaceBefore=8, spaceAfter=10),
    ]
    tokens = markdown().parse(snap.answer)
    story += b.blocks(tokens)
    if snap.sources:
        story += _heading("Sources", s)
        for src in snap.sources:
            label = escape(src.title or src.url)
            link = link_allowed(src.url, cited)
            body = f'<a href="{escape(src.url)}" color="#1c5d99">{label}</a>' if link else label
            story.append(Paragraph(f'<a name="src-{src.index}"/>{body}', s["source"],
                                   bulletText=f"{src.index}."))  # fmt: skip
    if snap.data:
        story += _heading("Data", s)
        rows = [[Paragraph(h, s["cellhead"]) for h in ("Record", "Call", "Source", "As of")]]
        for r in snap.data:
            asof = escape(r.as_of) + ("" if r.ok else ", failed")
            rows.append([Paragraph(escape(x), s["cell"]) for x in (r.label, r.call, r.source)]
                        + [Paragraph(asof, s["cell"])])  # fmt: skip
        width = LETTER[0] - 2 * _MARGIN
        table = Table(rows, colWidths=[width * f for f in (0.09, 0.5, 0.18, 0.23)], repeatRows=1)
        table.setStyle(_TABLE)
        story.append(table)
    elif snap.data_earlier:
        story += _heading("Data", s)
        story.append(Paragraph(escape(DATA_EARLIER), s["small"]))
    story += [HRFlowable(width="100%", thickness=0.6, color=_RULE, spaceBefore=14, spaceAfter=6),
              Paragraph(escape(DISCLAIMER), s["small"])]  # fmt: skip
    doc.build(story)
    return buffer.getvalue()
