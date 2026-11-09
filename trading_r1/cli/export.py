# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""`trading-r1 export`: a session's report as a PDF, an HTML page or markdown, in a file or sent to
channels."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trading_r1.cli.client import _err

_FORMAT_OF = {".pdf": "pdf", ".md": "md", ".markdown": "md", ".html": "html", ".htm": "html"}


def main(argv: list[str]) -> int:
    """``trading-r1 export SESSION``: a session's report as a PDF, an HTML page or markdown."""
    from trading_r1.report import PdfTextError, render_html, render_pdf, snapshot_from_session
    from trading_r1.runtime.session.store import (
        InvalidSessionIdError,
        SessionStore,
        default_sessions_dir,
    )

    ap = argparse.ArgumentParser(
        prog="trading-r1 export", description="Write a session's report to a file."
    )
    ap.add_argument("session", help="the session id (see the session list in the terminal)")
    ap.add_argument("--turn", type=int, default=None, help="the turn to export (default: last)")
    ap.add_argument(
        "--format",
        choices=["html", "md", "pdf"],
        default=None,
        help="default: from the output file's extension, else html",
    )
    ap.add_argument("-o", "--output", default=None, help="the file to write (default: stdout)")
    ap.add_argument(
        "--send",
        default=None,
        metavar="CHANNELS",
        help="send the report to these channels (e.g. email,telegram) through the running backend",
    )
    ap.add_argument("--key", default=None, help="with --send: repeat a send without duplicating it")
    args = ap.parse_args(argv)
    if args.send:
        import uuid

        from trading_r1.cli.channels import print_outcomes
        from trading_r1.cli.client import call_backend

        key = args.key or uuid.uuid4().hex
        _err(f"[trading-r1] send key {key} (repeat with --key {key} to be sure of one send)")
        body = {
            "channels": args.send.split(","),
            "key": key,
            **({"turn": args.turn} if args.turn else {}),
        }
        result = call_backend("POST", f"/sessions/{args.session}/send", body)
        return 1 if result is None else print_outcomes(result)
    try:
        snapshot = snapshot_from_session(
            SessionStore(default_sessions_dir()), args.session, args.turn
        )
    except InvalidSessionIdError as err:
        ap.error(str(err))
    if snapshot is None:
        _err(f"[trading-r1] no answered turn to export in session {args.session!r}")
        return 1
    output = Path(args.output) if args.output else None
    fmt = args.format or _FORMAT_OF.get(output.suffix.lower() if output else "", "html")
    if fmt == "pdf" and output is None:
        ap.error("--format pdf needs -o FILE")
    try:
        if fmt == "pdf":
            data = render_pdf(snapshot)
        else:
            text = render_html(snapshot) if fmt == "html" else snapshot.markdown()
            if output is None:
                sys.stdout.write(text)
                return 0
            data = text.encode()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    except PdfTextError as err:
        _err(
            f"[trading-r1] {err}. For an HTML page: trading-r1 export {args.session} --format html"
        )
        return 1
    except OSError as err:
        _err(f"[trading-r1] {output} could not be written: {err.strerror or err}")
        return 1
    _err(f"[trading-r1] wrote {output}")
    return 0
