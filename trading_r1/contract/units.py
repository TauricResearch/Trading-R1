# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Units: the closed set of value kinds a tool output carries, as annotated types.

A field typed ``Money`` holds a full-precision amount; its unit travels in the output's JSON
Schema (``"unit": "money"``), which is how the renderer formats it and how the capabilities
list documents it. Every unit is nullable, since market data has gaps, and a non-finite float
(NaN, infinity) becomes ``None`` at the boundary, so a payload is always valid JSON.

- ``Money``: an amount in the output's ``currency`` (or in the caller's units for compute).
- ``Price``: a level read to two decimals: per share or unit (a close, a target, EPS), or an
  index's (CPI 326.03).
- ``Fraction``: a ratio of like quantities (0.187 is 18.7%): margins, returns, rates, weights.
- ``Percent``: a rate a publisher states in percent (a Treasury yield of 4.25, a VIX level):
  kept as published, since converting would change the published figure.
- ``Points``: a change in percentage points (a yield up 0.25).
- ``Multiple``: a valuation or coverage multiple (EV/EBITDA 12.3).
- ``Count``: a quantity of things (shares, contracts, analysts).
- ``Number``: any other real (beta, t-statistic, a z-score, periods per year).
- ``Day``: an ISO date or timestamp.
- ``Code``: an identifier (ticker, currency, CIK, enum value).
- ``Text``: prose the model reads as written (a note, a snippet, a page).
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BeforeValidator, Field


class Unit(StrEnum):
    MONEY = "money"
    PRICE = "price"
    FRACTION = "fraction"
    PERCENT = "percent"
    POINTS = "points"
    MULTIPLE = "multiple"
    COUNT = "count"
    NUMBER = "number"
    EXACT = "exact"  # a figure read as it is: a calculator's result and inputs
    DAY = "day"
    CODE = "code"
    TEXT = "text"


def _finite(v: Any) -> Any:
    return None if isinstance(v, float) and not math.isfinite(v) else v


def _real(unit: Unit) -> Any:
    return Annotated[
        float | None, BeforeValidator(_finite), Field(json_schema_extra={"unit": unit.value})
    ]


def _string(unit: Unit) -> Any:
    return Annotated[str | None, Field(json_schema_extra={"unit": unit.value})]


Money = _real(Unit.MONEY)
Price = _real(Unit.PRICE)
Fraction = _real(Unit.FRACTION)
Percent = _real(Unit.PERCENT)
Points = _real(Unit.POINTS)
Multiple = _real(Unit.MULTIPLE)
Count = _real(Unit.COUNT)
Number = _real(Unit.NUMBER)
Exact = Annotated[
    int | float | None,
    BeforeValidator(_finite),
    Field(json_schema_extra={"unit": Unit.EXACT.value}),
]
Day = _string(Unit.DAY)
Code = _string(Unit.CODE)
Text = _string(Unit.TEXT)
