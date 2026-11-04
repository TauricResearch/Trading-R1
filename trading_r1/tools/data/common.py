# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for the data tools (equity + market): the instrument fields every price and
equity tool takes, and the number guard."""

from __future__ import annotations

import calendar
import datetime as dt
import numbers
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator, Field

from trading_r1.tools.data.symbols import canonical

__all__ = [
    "Symbol",
    "SymbolList",
    "TICKER_MAX_LEN",
    "is_num",
    "months_back",
]

# Max length for a ticker request field: permissive enough for a Tauric series key
# (series:us.equity_index.russell_2000); shared so every price tool agrees.
TICKER_MAX_LEN = 40


def _normalize(value: Any) -> Any:
    return canonical(value) if isinstance(value, str) else value


def _distinct(names: list[str]) -> list[str]:
    if len(set(names)) != len(names):
        raise ValueError("symbols must be distinct")
    return names


# One instrument name as the tools use it: `symbols.canonical` on the way in, so a handler (and
# the cache key and provenance built from it) sees the same spelling the data API does.
Symbol = Annotated[str, BeforeValidator(_normalize), Field(min_length=1, max_length=TICKER_MAX_LEN)]
# Several distinct instrument names (the caller bounds the count on the field).
SymbolList = Annotated[list[Symbol], AfterValidator(_distinct)]


def is_num(v: Any) -> bool:
    """True for a real, non-NaN number: the guard before any arithmetic. ``numbers.Real``
    covers Python and numpy numerics; bool is excluded."""
    return isinstance(v, numbers.Real) and not isinstance(v, bool) and v == v


def months_back(day: dt.date, months: int) -> dt.date:
    """`months` calendar months before `day`; a month end clamps (1 month before 31 March is
    28/29 February)."""
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    return day.replace(
        year=year, month=month + 1, day=min(day.day, calendar.monthrange(year, month + 1)[1])
    )
