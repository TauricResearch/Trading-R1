# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""A research turn as a document: the snapshot of one answer, and the page and PDF made from it."""

from trading_r1.report.html import render_html
from trading_r1.report.pdf import PdfTextError, render_pdf
from trading_r1.report.snapshot import ReportSnapshot, snapshot_from_session

__all__ = ["PdfTextError", "ReportSnapshot", "render_html", "render_pdf", "snapshot_from_session"]
