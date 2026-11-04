# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.equity.filings: a company's SEC filings, newest first, with links.

The EDGAR record Tauric keeps for a registrant: form, filing and report dates, the document's
link, and for an 8-K the items it reports, named (SEC Form 8-K items, a closed list). The model
reads a filing itself with `visit`.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec, hint
from trading_r1.contract.units import Code, Day, Text
from trading_r1.tools.data.common import Symbol
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "SEC filings for a US-listed company, newest first: form (10-K, 10-Q, 8-K, DEF 14A, 4, "
    "S-1, …), filing date, its report date (the period for a 10-K or 10-Q, the event for an "
    "8-K, the meeting for a proxy), the document link (open it with `visit` to "
    "read MD&A, risk factors, guidance or a press release), and for 8-Ks the items reported, "
    "named (2.02 results, 5.02 officer changes, 1.01 material agreements, …). Filter with "
    "`form`."
)
_PAGE = 100  # Tauric's most filings per response
# SEC Form 8-K items (17 CFR 249.308)
ITEMS_8K = {
    "1.01": "entry into a material agreement",
    "1.02": "termination of a material agreement",
    "1.03": "bankruptcy or receivership",
    "1.04": "mine safety: shutdowns and patterns of violations",
    "1.05": "material cybersecurity incident",
    "2.01": "acquisition or disposition of assets",
    "2.02": "results of operations",
    "2.03": "direct financial obligation",
    "2.04": "triggering events accelerating an obligation",
    "2.05": "exit or disposal costs",
    "2.06": "material impairments",
    "3.01": "delisting or listing-rule notice",
    "3.02": "unregistered sale of equity",
    "3.03": "modification of security holders' rights",
    "4.01": "change of auditor",
    "4.02": "non-reliance on previous financial statements",
    "5.01": "change in control",
    "5.02": "director or officer departure, election or appointment; compensation",
    "5.03": "amendment to articles or bylaws; fiscal year change",
    "5.04": "temporary suspension of trading under employee benefit plans",
    "5.05": "amendment to or waiver of the code of ethics",
    "5.06": "change in shell company status",
    "5.07": "shareholder vote results",
    "5.08": "shareholder director nominations",
    "6.01": "ABS informational and computational material",
    "6.02": "change of ABS servicer or trustee",
    "6.03": "change in ABS credit enhancement",
    "6.04": "failure to make a required ABS distribution",
    "6.05": "ABS securities act updating disclosure",
    "6.06": "static pool (ABS)",
    "7.01": "Regulation FD disclosure",
    "8.01": "other events",
    "9.01": "financial statements and exhibits",
}


class FilingsRequest(BaseModel):
    """Input for a company's SEC filings."""

    symbol: Symbol = Field(..., description="Company ticker (e.g. AAPL).")
    form: str | None = Field(
        None,
        max_length=24,
        description="Only this exact form, e.g. 10-K, 10-Q, 8-K (amendments are 10-K/A, 8-K/A). "
        "Without it the newest filings are mostly insider (4, 144) and prospectus (424B2) forms.",
    )
    limit: int = Field(15, ge=1, le=_PAGE, description="Filings to return, newest first.")


class Filing(BaseModel):
    """`report_date` is the fiscal period end for a 10-K/10-Q/20-F/40-F, else the event's
    date (an 8-K's event, a proxy's meeting)."""

    form: Code
    filed: Day
    report_date: Day = None
    url: Code = None
    accession: Annotated[Code, hint(hidden=True)] = None  # SEC's id, for chaining
    items: list[Code] = []  # an 8-K's items: number and name, e.g. "2.02 Results of …"


class FilingsOutput(Output):
    ticker: Code
    form: Code = None
    filings: Annotated[list[Filing], hint(rows=_PAGE)]  # every filing asked for is shown
    notes: list[Text] = []


def _filing(f: dict[str, Any]) -> dict[str, Any]:
    items = f.get("items") or []
    return {
        "form": f.get("form"),
        "filed": f.get("filed"),
        "report_date": f.get("report_date"),
        "url": f.get("url"),
        "accession": f.get("accession"),
        "items": [f"{i} {ITEMS_8K[i]}" if i in ITEMS_8K else i for i in items],
    }


async def _handler(req: FilingsRequest) -> Result:
    ticker = req.symbol
    async with Tauric() as api:
        body = await api.get(
            f"/securities/{ticker}/filings", form=req.form and req.form.upper(), limit=req.limit
        )
    filings = [_filing(f) for f in body.get("data") or []]
    if not filings:
        if req.form:
            raise ToolError(f"no {req.form} filings for '{ticker}': check the form name")
        raise ToolError(f"no filings for '{ticker}' (SEC registrants only)")
    return Result(
        FilingsOutput(ticker=ticker, form=req.form, filings=filings, notes=api.limits),
        Provenance(
            **provenance(
                subject=ticker, view=req.form, as_of=filings[0]["filed"], source="SEC EDGAR"
            )
        ),
    )


SPEC = ToolSpec(
    name="data_equity_filings",
    display_name="data.equity.filings",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=FilingsRequest,
    handler=_handler,
    output=FilingsOutput,
    tags=("equity", "filings", "sec", "edgar", "8-k", "10-k", "data"),
)
