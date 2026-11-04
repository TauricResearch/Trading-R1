# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The report as one self-contained HTML page: no script, no external request, its own styles.

The answer's markdown is rendered with raw HTML and images off; links keep only http(s) and
in-page targets. ``[N]`` markers become links to the numbered source. A Content-Security-Policy
meta forbids every fetch and script, so a page that reaches an inbox or a browser can run
nothing.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from markdown_it.token import Token

from trading_r1.report.common import (
    MARKER,
    link_allowed,
    link_scope,
    markdown,
    when,
)
from trading_r1.report.snapshot import DATA_EARLIER, DISCLAIMER, ReportSnapshot

_LOGO = (
    Path(__file__)
    .with_name("logo.svg")
    .read_text(encoding="utf-8")
    .replace("<svg ", '<svg class="logo" role="img" aria-label="Trading-R1" ', 1)
)

_LINKS = "".join(
    f'<a href="{url}" rel="noopener">{name}</a>'
    for name, url in (
        ("Tauric Research", "https://tauric.ai"),
        ("GitHub", "https://github.com/TauricResearch/Trading-R1"),
    )
)

_CSS = """
:root{--paper:#fbfaf7;--ink:#1d1d1b;--muted:#6a6a64;--rule:#dcdad2;--accent:#1c5d99;--wash:#f1efe8;--brand:#14c290;--brandink:#0b8a65;--dots:#e2e2e2}
@media (prefers-color-scheme:dark){:root{--paper:#16171a;--ink:#e6e4de;--muted:#9a988f;
--rule:#2f3035;--accent:#7db3e8;--wash:#1f2024;--brandink:#14c290;--dots:#3a3c40}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:17px/1.62 Charter,"Iowan Old Style",
Georgia,serif}
main{max-width:46rem;margin:0 auto;padding:2.5rem 1rem 4rem}
.sans,header,h2,table,.meta,footer,.kicker{font-family:ui-sans-serif,system-ui,-apple-system,
"Segoe UI",Helvetica,Arial,sans-serif}
.logo{display:block;height:3.6rem;max-width:100%;width:auto;margin:0 0 1.8rem}
.logo [stroke="#E2E2E2"]{stroke:var(--dots)}
.kicker{margin:0;color:var(--muted);font-size:.8rem;letter-spacing:.08em;text-transform:uppercase}
.question{margin:.4rem 0 1rem;font-size:1.1rem;color:var(--muted);font-style:italic}
.meta{display:flex;flex-wrap:wrap;gap:.3rem 1.6rem;margin:0 0 2rem;padding:.8rem 0;
border-block:1px solid var(--rule);font-size:.82rem}
.meta div{display:flex;gap:.4rem}.meta dt{color:var(--muted)}.meta dd{margin:0}
h1,h2,h3{line-height:1.25}h1{font-size:1.85rem;margin:1.6rem 0 .8rem}
h2{font-size:1.2rem;margin:2rem 0 .6rem;padding-top:.7rem;border-top:2px solid var(--brand)}
h1,h2,h3,section{scroll-margin-top:1rem}h3{font-size:1.02rem}
a{color:var(--accent)}sup a,a.cite{text-decoration:none;font-size:.82em}
code{background:var(--wash);padding:.05em .3em;border-radius:3px;font-size:.9em}
pre{background:var(--wash);padding:.8rem;overflow-x:auto;border-radius:4px}pre code{padding:0}
blockquote{margin:1rem 0;padding:.1rem 1rem;border-left:3px solid var(--rule);color:var(--muted)}
.scroll{overflow-x:auto;margin:1rem 0}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{padding:.35rem .6rem;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums}
ol.sources{padding-left:1.4rem;font-size:.92rem}ol.sources .host{color:var(--muted)}
.bad{color:#b3261e}
footer .links{margin:0 0 .6rem;font-size:.9rem;text-align:right}
footer .links a{color:var(--brandink);font-weight:600;text-decoration:none;margin-left:1.2rem}
footer .links a:hover{text-decoration:underline}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--rule);color:var(--muted);
font-size:.8rem}
.contents{margin:0 0 2rem;font:.85rem sans-serif}
.contents ol{display:flex;flex-wrap:wrap;gap:.2rem 1.2rem;margin:0;padding:0;list-style:none}
.top{position:fixed;right:1rem;bottom:1rem;padding:.25rem .6rem;border-radius:1rem;
background:var(--wash);border:1px solid var(--rule);font:.78rem sans-serif;text-decoration:none}
@media (min-width:72rem){
.contents{position:fixed;left:max(1rem,calc(50% - 23rem - 15rem));top:6rem;width:13rem;margin:0}
.contents ol{display:block;border-left:2px solid var(--rule)}
.contents li{margin:0}.contents a{display:block;padding:.2rem .8rem;color:var(--muted);
text-decoration:none}.contents a:hover,.contents a:focus{color:var(--ink)}}
@media print{body{background:#fff;color:#000;font-size:11pt}main{max-width:none;padding:0}
a{color:#000}.scroll{overflow:visible}tr,li{break-inside:avoid}.contents,.top{display:none}}
"""


def _link(href: str, label: str) -> list[Token]:
    opened = Token("link_open", "a", 1)
    opened.attrs = {"href": href, "class": "cite"}
    text = Token("text", "", 0)
    text.content = label
    return [opened, text, Token("link_close", "a", -1)]


def _link_markers(tokens: list[Token], known: set[int]) -> None:
    """Turn ``[N]`` in running text into links to source N; links and code are left alone."""
    for block in tokens:
        if block.type != "inline" or not block.children:
            continue
        out: list[Token] = []
        inside_link = 0
        for child in block.children:
            inside_link += {"link_open": 1, "link_close": -1}.get(child.type, 0)
            if child.type != "text" or inside_link or not MARKER.search(child.content):
                out.append(child)
                continue
            pos = 0
            for m in MARKER.finditer(child.content):
                if int(m.group(1)) not in known:
                    continue
                if m.start() > pos:
                    plain = Token("text", "", 0)
                    plain.content = child.content[pos : m.start()]
                    out.append(plain)
                out.extend(_link(f"#src-{int(m.group(1))}", m.group(0)))
                pos = m.end()
            if pos < len(child.content):
                tail = Token("text", "", 0)
                tail.content = child.content[pos:]
                out.append(tail)
        block.children = out


def _restrict_links(tokens: list[Token], cited: frozenset[str] | None) -> None:
    """Keep a link's target only if it is http(s) or in-page, and, when ``cited`` is given, only
    if the report cites that page; mark outside links."""
    for block in tokens:
        for child in block.children or ():
            if child.type != "link_open":
                continue
            href = str(child.attrGet("href") or "")
            if href.startswith("#"):
                continue
            if link_allowed(href, cited):
                child.attrSet("rel", "noopener noreferrer nofollow")
            else:
                child.attrs = {}


def _number_sections(tokens: list[Token]) -> list[tuple[str, str]]:
    """Give each ``##`` heading of the answer an id and return ``(id, text)`` for the contents."""
    found: list[tuple[str, str]] = []
    for i, tok in enumerate(tokens[:-1]):
        if tok.type == "heading_open" and tok.tag == "h2" and tokens[i + 1].type == "inline":
            ident = f"sec-{len(found) + 1}"
            tok.attrSet("id", ident)
            text = "".join(
                c.content for c in tokens[i + 1].children or () if c.type in ("text", "code_inline")
            )
            found.append((ident, text))
    return found


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def render_html(snap: ReportSnapshot, *, links: Literal["web", "cited"] = "web") -> str:
    """The report as an HTML document. ``links="cited"`` (a page that is sent to someone) keeps
    only the links to pages the report cites; ``"web"`` keeps every http(s) link."""
    cited = link_scope(snap, links)
    md = markdown()
    tokens = md.parse(snap.answer)
    _link_markers(tokens, {s.index for s in snap.sources})
    _restrict_links(tokens, cited)
    sections = _number_sections(tokens)
    body = md.renderer.render(tokens, md.options, {})
    body = body.replace("<table>", '<div class="scroll"><table>').replace(
        "</table>", "</table></div>"
    )
    title = next(
        (
            "".join(c.content for c in t.children if c.type in ("text", "code_inline"))
            for i, t in enumerate(tokens)
            if t.type == "inline"
            and t.children
            and i
            and tokens[i - 1].type == "heading_open"
            and tokens[i - 1].tag == "h1"
        ),
        snap.question,
    )
    meta = [
        ("Model", snap.model),
        ("Mode", snap.mode),
        ("Date", when(snap.created)),
        ("Version", snap.version),
    ]
    facts = "".join(
        f"<div><dt>{label}</dt><dd>{_esc(value)}</dd></div>" for label, value in meta if value
    )
    sources = ""
    if snap.sources:
        items = "".join(_source_item(s, cited) for s in snap.sources)
        sources = (
            f'<section id="sources"><h2>Sources</h2><ol class="sources">{items}</ol></section>'
        )
    data = ""
    if snap.data:
        rows = "".join(
            f"<tr><td>{_esc(r.label)}</td><td>{_esc(r.call)}</td><td>{_esc(r.source)}</td>"
            f'<td class="{"" if r.ok else "bad"}">{_esc(r.as_of)}{"" if r.ok else ", failed"}</td>'
            "</tr>"
            for r in snap.data
        )
        data = (
            '<section id="data"><h2>Data</h2><div class="scroll"><table><thead><tr><th>Record</th>'
            "<th>Call</th><th>Source</th><th>As of</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div></section>"
        )
    elif snap.data_earlier:
        data = f'<section id="data"><h2>Data</h2><p>{_esc(DATA_EARLIER)}</p></section>'
    if snap.sources:
        sections.append(("sources", "Sources"))
    if snap.data or snap.data_earlier:
        sections.append(("data", "Data"))
    contents = ""
    if len(sections) > 1:
        entries = "".join(f'<li><a href="#{i}">{_esc(t)}</a></li>' for i, t in sections)
        contents = f'<nav class="contents" aria-label="Contents"><ol>{entries}</ol></nav>'
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body><main>"
        f'<header id="top">{_LOGO}<p class="kicker">Research report</p>'
        f'<p class="question">{_esc(snap.question)}</p><dl class="meta">{facts}</dl></header>'
        f'{contents}<article>{body}</article>{sources}{data}<a class="top" href="#top">Top</a>'
        f'<footer><p class="links">{_LINKS}</p>{_esc(DISCLAIMER)}</footer>'
        "</main></body></html>"
    )


def _source_item(s, cited: frozenset[str] | None) -> str:  # noqa: ANN001 (a Source)
    label = _esc(s.title or s.url)
    if not link_allowed(s.url, cited):
        return f'<li value="{s.index}" id="src-{s.index}">{label}</li>'
    try:
        host = urlsplit(s.url).hostname or ""
    except ValueError:
        host = ""
    return (
        f'<li value="{s.index}" id="src-{s.index}">'
        f'<a href="{_esc(s.url)}" rel="noopener noreferrer nofollow">{label}</a> '
        f'<span class="host">{_esc(host)}</span></li>'
    )
