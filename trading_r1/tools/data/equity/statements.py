# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Financial statements for data.equity.fundamentals, from Tauric's SEC-filing facts.

Tauric serves XBRL facts by concept (`us-gaap:Revenues`, …), each fact a value over a period of
one class: annual (a 10-K year), quarterly, ytd (cumulative since the fiscal year began) or
instant (a balance-sheet date). This module is the standard model over them, in three parts:

- The period engine turns facts into columns: fiscal years (as a 10-K files them), quarters
  (a quarter filed only inside a cumulative, which is every fiscal Q4, and Q2 and Q3 of a 10-Q's
  cash flows, is the cumulative less the one a quarter before), balance-sheet dates, and the
  trailing twelve months (the latest year plus the current cumulative less last year's).
- Line definitions (`LINES`): each standard line is an ordered list of sources, each a
  US-GAAP concept or a `Rule` over other lines: gross profit is `GrossProfit` as filed, else
  revenue less cost of revenue (the identities of XBRL US's Fundamental Accounting Concepts).
  For every period, the first source with a value there wins, so a filer that changed concepts
  keeps one series and a line is never mixed from different windows.
- Basis: every line says where its values come from (the concept, or the rule's formula),
  and an identity whose parts are all filed is checked; a mismatch is reported, never
  corrected.

Some alternatives are outside the concept set Tauric serves; they are listed where filers
use them and take effect when served. Facts are read in Tauric's `continuity` view: a
registrant succession (a new holding company taking over the filings) keeps the predecessor's
history. Filers outside US-GAAP (foreign private issuers on IFRS) have no such facts and are
reported as not served.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from trading_r1.tools.data.tauric import Tauric

_G = "us-gaap:"


@dataclass(frozen=True)
class Rule:
    """A line derived from other lines of the same period: `fn` over `inputs`, in order."""

    formula: str  # how the basis reads, e.g. "revenue − cost of revenue"
    inputs: tuple[str, ...]
    fn: Callable[..., float]
    identity: bool = False  # an accounting identity: checked when the line is also filed
    industrial: bool = False  # not for interest-based (bank) statements


@dataclass(frozen=True)
class Line:
    """A standard line: its sources in order of preference, and how it aggregates."""

    key: str
    sources: tuple[str | Rule, ...]
    kind: str = "flow"  # flow | stock | per_share | count
    outflow: bool = False  # filed as a positive payment, shown negative
    # its concepts name one figure that filers move between (even between a 10-K and the
    # 10-Qs around it), so a trailing year may join them
    synonyms: bool = False


def _concepts(*names: str) -> tuple[str, ...]:
    return tuple(_G + n for n in names)


_GROSS = Rule(
    "revenue − cost of revenue",
    ("revenue", "cost_of_revenue"),
    lambda r, c: r - c,
    identity=True,
    industrial=True,
)
_OPERATING = Rule(
    "gross profit − operating expenses",
    ("gross_profit", "operating_expenses"),
    lambda g, o: g - o,
    identity=True,
)
_DA_PARTS = Rule(
    "depreciation + amortization of intangibles",
    ("depreciation", "amortization_of_intangibles"),
    lambda d, a: d + a,
)
_EBITDA_OPERATING = Rule(
    "operating income + D&A",
    ("operating_income", "depreciation_and_amortization"),
    lambda o, d: o + d,
    industrial=True,
)
_EBITDA_PRETAX = Rule(
    "pretax income + interest expense + D&A",
    ("pretax_income", "interest_expense", "depreciation_and_amortization"),
    lambda p, i, d: p + i + d,
    industrial=True,
)
_DEBT = Rule(
    "long-term debt + current debt",
    ("long_term_debt", "current_debt"),
    lambda lt, c: lt + c,
)
_WORKING_CAPITAL = Rule(
    "current assets − current liabilities",
    ("current_assets", "current_liabilities"),
    lambda a, b: a - b,
)
_FREE_CASH_FLOW = Rule(
    "operating cash flow − capital expenditure",
    ("operating_cash_flow", "capital_expenditure"),
    lambda o, c: o + c,  # capex is carried negative
)

# D&A as the cash flow statement files it: whole, or as its two parts.
_DA_SOURCES = (
    *_concepts("DepreciationDepletionAndAmortization", "DepreciationAndAmortization"),
    _DA_PARTS,
)

LINES: dict[str, tuple[Line, ...]] = {
    "income": (
        Line(
            "revenue",
            _concepts(
                "RevenuesNetOfInterestExpense",  # banks' total net revenue
                "Revenues",
                "RevenueFromContractWithCustomerExcludingAssessedTax",
            ),
        ),
        Line(
            "cost_of_revenue",
            _concepts("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"),
        ),
        Line("gross_profit", (*_concepts("GrossProfit"), _GROSS)),
        Line(
            "research_and_development",
            _concepts(
                # filers that also tag acquired in-process R&D put the line itself here
                "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
                "ResearchAndDevelopmentExpense",
            ),
        ),
        Line("sga", _concepts("SellingGeneralAndAdministrativeExpense")),
        Line("operating_expenses", _concepts("OperatingExpenses")),
        Line("operating_income", (*_concepts("OperatingIncomeLoss"), _OPERATING)),
        Line(
            "interest_expense",
            _concepts("InterestExpenseNonoperating", "InterestExpense", "InterestExpenseDebt"),
        ),
        Line(
            "pretax_income",
            _concepts(
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
            ),
        ),
        Line("tax_provision", _concepts("IncomeTaxExpenseBenefit")),
        Line("net_income", _concepts("NetIncomeLoss", "ProfitLoss")),
        Line("depreciation", _concepts("Depreciation")),
        Line("amortization_of_intangibles", _concepts("AmortizationOfIntangibleAssets")),
        Line("depreciation_and_amortization", _DA_SOURCES),
        Line("ebitda", (_EBITDA_OPERATING, _EBITDA_PRETAX)),
        Line("diluted_eps", _concepts("EarningsPerShareDiluted"), kind="per_share"),
        Line(
            "diluted_shares",
            _concepts("WeightedAverageNumberOfDilutedSharesOutstanding"),
            kind="count",
        ),
    ),
    "balance": (
        Line("cash", _concepts("CashAndCashEquivalentsAtCarryingValue"), kind="stock"),
        Line(
            "receivables",
            _concepts("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
            kind="stock",
        ),
        Line("inventory", _concepts("InventoryNet"), kind="stock"),
        Line("current_assets", _concepts("AssetsCurrent"), kind="stock"),
        Line(
            "net_ppe",
            _concepts(
                "PropertyPlantAndEquipmentNet",
                "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
            ),
            kind="stock",
        ),
        Line("goodwill", _concepts("Goodwill"), kind="stock"),
        Line("intangibles", _concepts("IntangibleAssetsNetExcludingGoodwill"), kind="stock"),
        Line("total_assets", _concepts("Assets"), kind="stock"),
        Line(
            "payables",
            _concepts("AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"),
            kind="stock",
        ),
        Line(
            "current_debt",
            _concepts(
                "LongTermDebtCurrent",
                "DebtCurrent",
                "LongTermDebtAndCapitalLeaseObligationsCurrent",
                "ShortTermBorrowings",
            ),
            kind="stock",
        ),
        Line("current_liabilities", _concepts("LiabilitiesCurrent"), kind="stock"),
        Line("long_term_debt", _concepts("LongTermDebtNoncurrent"), kind="stock"),
        Line("total_debt", (_DEBT, *_concepts("LongTermDebt")), kind="stock"),
        Line("total_liabilities", _concepts("Liabilities"), kind="stock"),
        Line(
            "stockholders_equity",
            _concepts(
                "StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            ),
            kind="stock",
        ),
        Line("working_capital", (_WORKING_CAPITAL,), kind="stock"),
    ),
    "cashflow": (
        Line("operating_cash_flow", _concepts("NetCashProvidedByUsedInOperatingActivities")),
        Line("depreciation", _concepts("Depreciation")),
        Line("amortization_of_intangibles", _concepts("AmortizationOfIntangibleAssets")),
        Line("depreciation_and_amortization", _DA_SOURCES),
        Line(
            "stock_based_compensation",
            _concepts("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
        ),
        Line(
            "capital_expenditure",
            _concepts(
                "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"
            ),
            outflow=True,
            synonyms=True,
        ),
        Line("free_cash_flow", (_FREE_CASH_FLOW,)),
        Line("investing_cash_flow", _concepts("NetCashProvidedByUsedInInvestingActivities")),
        Line("financing_cash_flow", _concepts("NetCashProvidedByUsedInFinancingActivities")),
        Line("dividends_paid", _concepts("PaymentsOfDividends"), outflow=True),
        Line("share_repurchases", _concepts("PaymentsForRepurchaseOfCommonStock"), outflow=True),
    ),
}
# Lines that make the statement's bookkeeping but not its table (the parts of D&A).
_HIDDEN = frozenset({"depreciation", "amortization_of_intangibles"})
# A column is a real period when any of its statement's anchor lines is reported.
ANCHORS = {
    "income": ("revenue", "net_income"),
    "balance": ("total_assets",),
    "cashflow": ("operating_cash_flow",),
}
# How derived figures render (everything else is a %: margins, growth, rates).
MULTIPLES = frozenset({"current_ratio", "debt_to_equity"})
# How a basis marks a figure a rule computed (a filed figure's basis is its concept).
DERIVED = " (derived)"
# Interest-based (bank) statements: their revenue is net of interest expense.
_INTEREST_BASED = _G + "RevenuesNetOfInterestExpense"
# Facts per response is bounded by Tauric (values × concepts); requests are split to fit.
_FACTS_PER_CALL = 100
_MAX_LIMIT = 40  # Tauric's most values per concept in one response
# A quarter and a year in days, with the slack 52/53-week years need.
_QUARTER_GAP = range(80, 106)
_YEAR_DAYS = range(350, 380)
# An identity holds when its two sides agree within this fraction of the filed value.
_IDENTITY_TOLERANCE = 0.01

Fact = dict[str, Any]
Facts = dict[str, dict[str, Any]]  # concept to Tauric's entry (unit, last_period_end, values)
Series = list[float | None]
Column = dict[str, float]  # concept to its value in one period


def line(statement: str, key: str) -> Line:
    return next(ln for ln in LINES[statement] if ln.key == key)


def _closure(statement: str, keys: tuple[str, ...] | None) -> tuple[Line, ...]:
    """The statement's lines needed for `keys` (all of them for None): the keys and every
    line their rules read, in definition order."""
    if keys is None:
        return LINES[statement]
    need: set[str] = set()
    todo = list(keys)
    while todo:
        key = todo.pop()
        if key in need:
            continue
        need.add(key)
        todo += [i for s in line(statement, key).sources if isinstance(s, Rule) for i in s.inputs]
    return tuple(ln for ln in LINES[statement] if ln.key in need)


def wanted(statement: str, keys: tuple[str, ...] | None = None) -> list[str]:
    """The concepts to request for a statement's lines (all of them, or `keys` and what their
    rules read)."""
    return sorted({s for ln in _closure(statement, keys) for s in ln.sources if isinstance(s, str)})


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #


async def fetch_facts(
    api: Tauric, ticker: str, concepts: list[str], period_type: str, limit: int
) -> Facts:
    """The newest `limit` facts of `period_type` for each concept, split across requests so
    each response stays within Tauric's size bound."""
    per_call = max(1, _FACTS_PER_CALL // limit)
    groups = [concepts[i : i + per_call] for i in range(0, len(concepts), per_call)]
    bodies = await asyncio.gather(
        *(
            api.get(
                f"/securities/{ticker}/financials",
                concepts=",".join(g),
                period_type=period_type,
                limit=limit,
                view="continuity",
            )
            for g in groups
        )
    )
    return {k: v for body in bodies for k, v in (body.get("concepts") or {}).items()}


def limits_for(statement: str, quarterly: bool, periods: int) -> dict[str, int]:
    """How many facts of each period type a statement needs for `periods` periods: quarterly
    flows also take the years and cumulative figures their derived quarters come from (Q2 and
    Q3 are each a cumulative less the one before it: one more cumulative than quarters)."""
    if statement == "balance":
        return {"instant" if quarterly else "annual": periods}
    if quarterly:
        return {"quarterly": periods, "annual": years(periods // 4 + 2), "ytd": periods + 1}
    return {"annual": years(periods)}


def years(n: int) -> int:
    """The annual-class facts to ask for to see `n` fiscal years: one each (the class holds
    the 10-K years alone; a 10-Q's trailing-year windows are the `ttm` class)."""
    return min(n, _MAX_LIMIT)


async def load_facts(
    api: Tauric,
    ticker: str,
    statement: str,
    limits: dict[str, int],
    keys: tuple[str, ...] | None = None,
) -> dict[str, Facts]:
    """A statement's facts by period type, fetched in parallel; `limits` maps annual / quarterly /
    ytd / instant to how many of the newest facts each concept needs."""
    concepts = wanted(statement, keys)
    got = await asyncio.gather(
        *(fetch_facts(api, ticker, concepts, ptype, n) for ptype, n in limits.items())
    )
    return dict(zip(limits, got, strict=True))


def currency(facts: Facts) -> str | None:
    """The reporting currency: the unit of any monetary concept."""
    units = (e.get("unit") or "" for e in facts.values())
    return next((u for u in units if u and "/" not in u and u != "shares"), None)


# --------------------------------------------------------------------------- #
# the period engine
# --------------------------------------------------------------------------- #


def _days(a: str, b: str) -> int:
    return (dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days


def _values(facts: Facts, concept: str) -> list[Fact]:
    return [v for v in (facts.get(concept) or {}).get("values") or [] if v.get("value") is not None]


def _years(facts: dict[str, Facts], concept: str) -> list[Fact]:
    """A concept's fiscal years: annual facts a 10-K filed (not a 10-Q's trailing window)."""
    return [v for v in _values(facts.get("annual", {}), concept) if v["form"].startswith("10-K")]


def _by_end(values: list[Fact]) -> dict[str, float]:
    out: dict[str, float] = {}
    for v in values:  # a restated period appears twice: the first listed wins, as in ttm
        out.setdefault(v["period_end"], v["value"])
    return out


def _discrete_quarters(facts: list[Fact]) -> dict[str, float]:
    """Quarters filed only as cumulatives (a 10-Q's cash flow, every fiscal Q4): within one
    fiscal year (same start), each cumulative less the one a quarter before it."""
    by_start: dict[str, list[Fact]] = {}
    for f in facts:
        if f.get("period_start"):
            by_start.setdefault(f["period_start"], []).append(f)
    out: dict[str, float] = {}
    for group in by_start.values():
        group.sort(key=lambda f: f["period_end"])
        for prev, cur in zip(group, group[1:], strict=False):
            if _days(cur["period_end"], prev["period_end"]) in _QUARTER_GAP:
                out[cur["period_end"]] = cur["value"] - prev["value"]
    return out


def concept_series(kind: str, facts: dict[str, Facts], concept: str, ln: Line) -> Column:
    """One concept as {period_end: value} for a statement kind (annual, quarterly or balance).
    `facts` holds Tauric's facts by period type; an additive quarterly line adds the quarters
    that are filed only as cumulatives."""
    if kind == "balance":
        return _by_end(_values(facts.get("instant", {}), concept))
    if kind == "annual":
        return _by_end(_years(facts, concept))
    out = _by_end(_values(facts.get("quarterly", {}), concept))
    if ln.kind == "flow":
        cumulative = [
            *_years(facts, concept),
            *_values(facts.get("ytd", {}), concept),
            *_values(facts.get("quarterly", {}), concept),
        ]
        out |= {e: v for e, v in _discrete_quarters(cumulative).items() if e not in out}
    return out


def _first_per_window(values: list[Fact]) -> list[Fact]:
    """One fact per (start, end) window: the first listed, i.e. the preferred concept's."""
    seen: dict[tuple[Any, str], Fact] = {}
    for v in values:
        seen.setdefault((v.get("period_start"), v["period_end"]), v)
    return list(seen.values())


def ttm(facts: dict[str, Facts], *concepts: str) -> tuple[str, float] | None:
    """A flow's trailing twelve months as (end, value): the latest filed year, or that year
    plus the current cumulative less last year's same cumulative. Several concepts are joined,
    the first preferred for any window both file (synonyms only: `Line.synonyms`)."""
    annual = _first_per_window([a for c in concepts for a in _years(facts, c)])
    cumulative = _first_per_window(
        [
            v
            for c in concepts
            for v in _values(facts.get("ytd", {}), c) + _values(facts.get("quarterly", {}), c)
        ]
    )
    if not cumulative and not annual:
        return None
    latest = max([v["period_end"] for v in cumulative] + [a["period_end"] for a in annual])
    year = next((a for a in annual if a["period_end"] == latest), None)
    if year is not None:
        return latest, year["value"]
    # the longest cumulative ending at the latest date runs from the fiscal year's start
    current = max(
        (v for v in cumulative if v["period_end"] == latest), key=lambda v: v["duration_days"]
    )
    prior = next(
        (
            v
            for v in cumulative
            if _days(latest, v["period_end"]) in _YEAR_DAYS
            and abs(v["duration_days"] - current["duration_days"]) <= 10
        ),
        None,
    )
    last_year = next(
        (a for a in annual if _days(current["period_start"], a["period_end"]) in range(0, 15)),
        None,
    )
    if prior is None or last_year is None:
        return None
    return latest, last_year["value"] + current["value"] - prior["value"]


# --------------------------------------------------------------------------- #
# lines
# --------------------------------------------------------------------------- #


def interest_based(facts: dict[str, Facts]) -> bool:
    """A bank's statements: revenue filed net of interest expense."""
    return any(_values(f, _INTEREST_BASED) for f in facts.values())


def resolve(
    lines: tuple[Line, ...], column: Column, *, bank: bool
) -> tuple[dict[str, float], dict[str, str], list[dict[str, Any]]]:
    """One period's standard lines from its concept values: (values, basis, checks). A line
    takes its first source with a value; a rule reads lines resolved before it. Where a line
    is filed and its identity can also be computed, the two are compared."""
    values: dict[str, float] = {}
    basis: dict[str, str] = {}
    checks: list[dict[str, Any]] = []
    for ln in lines:
        sign = -1.0 if ln.outflow else 1.0
        for source in ln.sources:
            got = _evaluate(source, column, values, bank)
            if got is None:
                continue
            if ln.key not in values:
                values[ln.key] = sign * got if isinstance(source, str) else got
                basis[ln.key] = _label(source)
            elif isinstance(source, Rule) and source.identity:
                filed = values[ln.key]
                if abs(got - filed) > _IDENTITY_TOLERANCE * abs(filed):
                    checks.append(
                        {"line": ln.key, "filed": filed, "derived": got, "rule": source.formula}
                    )
    return values, basis, checks


def _evaluate(
    source: str | Rule, column: Column, values: dict[str, float], bank: bool
) -> float | None:
    if isinstance(source, str):
        return column.get(source)
    if source.industrial and bank:
        return None
    args = [values.get(i) for i in source.inputs]
    return None if None in args else source.fn(*args)


def _label(source: str | Rule) -> str:
    return source.removeprefix(_G) if isinstance(source, str) else source.formula + DERIVED


def statement_series(
    facts: dict[str, Facts],
    statement: str,
    periods: int,
    quarterly: bool,
    keys: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """The newest `periods` periods of a statement, oldest first, as {periods, lines, basis,
    derived, checks}; a line a company's filings lack is left out."""
    kind = "balance" if statement == "balance" else ("quarterly" if quarterly else "annual")
    defs = _closure(statement, keys)
    by_concept = {
        c: concept_series(kind, facts, c, ln)
        for ln in defs
        for c in ln.sources
        if isinstance(c, str)
    }
    columns: dict[str, Column] = {}
    for concept, series in by_concept.items():
        for end, value in series.items():
            columns.setdefault(end, {})[concept] = value
    bank = interest_based(facts)
    resolved = {end: resolve(defs, col, bank=bank) for end, col in columns.items()}
    anchors = ANCHORS[statement]
    ends = sorted(e for e, (v, _, _) in resolved.items() if any(a in v for a in anchors))
    ends = ends[-periods:]
    shown = [ln for ln in defs if ln.key not in _HIDDEN and (keys is None or ln.key in keys)]
    lines: dict[str, Series] = {}
    basis: dict[str, str] = {}
    for ln in shown:
        col = [resolved[e][0].get(ln.key) for e in ends]
        if any(v is not None for v in col):
            lines[ln.key] = col
            used = dict.fromkeys(resolved[e][1][ln.key] for e in ends if ln.key in resolved[e][1])
            basis[ln.key] = "; ".join(used)
    return {
        "periods": ends,
        "lines": lines,
        "basis": basis,
        "derived": derived(statement, lines, ends, quarterly) if lines else {},
        "checks": [dict(c, period=e) for e in ends for c in resolved[e][2]],
    }


def ttm_lines(
    facts: dict[str, Facts], statement: str, keys: tuple[str, ...]
) -> tuple[dict[str, float], dict[str, str], str | None]:
    """Trailing-twelve-month values of the named lines, as (values, basis, as_of). Every
    concept enters at the statement's newest date only: a concept the filer stopped reporting,
    or one filed to an earlier date, is not combined with current figures."""
    defs = _closure(statement, keys)
    sums = {
        c: got
        for ln in defs
        if ln.kind in ("flow", "per_share")
        for c in ln.sources
        if isinstance(c, str) and (got := ttm(facts, c)) is not None
    }
    anchors = [c for a in ANCHORS[statement] for c in line(statement, a).sources if c in sums]
    if not anchors:
        return {}, {}, None
    end = max(sums[c][0] for c in anchors)
    column = {c: v for c, (e, v) in sums.items() if e == end}
    joined: dict[str, str] = {}
    for ln in defs:
        names = [c for c in ln.sources if isinstance(c, str)]
        if ln.synonyms and not any(c in column for c in names):
            got = ttm(facts, *names)
            if got is not None and got[0] == end:
                column[names[0]] = got[1]
                joined[ln.key] = " joined with ".join(_label(c) for c in names)
    values, basis, _ = resolve(defs, column, bank=interest_based(facts))
    basis |= {k: v for k, v in joined.items() if k in basis}
    keep = [k for k in keys if k in values]
    return {k: values[k] for k in keep}, {k: basis[k] for k in keep}, end


# --------------------------------------------------------------------------- #
# per-period figures
# --------------------------------------------------------------------------- #


def _ratio(num: float | None, den: float | None) -> float | None:
    return num / den if num is not None and den else None


def _positive_base(num: float | None, den: float | None) -> float | None:
    """A ratio defined only over a positive base: a tax rate on a pretax loss, or debt over
    negative equity, is not the quantity its name promises."""
    return num / den if num is not None and den is not None and den > 0 else None


def _change(prev: float | None, cur: float | None) -> float | None:
    """Change as a fraction of |prev|: sign-safe, so a shrinking loss is a rise."""
    return (cur - prev) / abs(prev) if cur is not None and prev else None


def _sequential(series: Series) -> Series:
    return [None, *(_change(p, c) for p, c in zip(series, series[1:], strict=False))]


def _year_on_year(series: Series, dates: list[str]) -> Series:
    """Change against the period dated about a year earlier (quarterly data has gaps)."""
    days = [dt.date.fromisoformat(d) for d in dates]
    out: Series = []
    for i, day in enumerate(days):
        prior = next((j for j in range(i) if (day - days[j]).days in _YEAR_DAYS), None)
        out.append(None if prior is None else _change(series[prior], series[i]))
    return out


def derived(statement: str, lines: dict[str, Series], dates: list[str], quarterly: bool) -> dict:
    """Named per-period ratios and growth from a statement's own lines."""

    def col(key: str) -> Series:
        return lines.get(key, [None] * len(dates))

    def pairs(a: str, b: str):
        return zip(col(a), col(b), strict=True)

    if statement == "income":
        out = {
            "gross_margin": [_ratio(g, r) for g, r in pairs("gross_profit", "revenue")],
            "operating_margin": [_ratio(o, r) for o, r in pairs("operating_income", "revenue")],
            "net_margin": [_ratio(i, r) for i, r in pairs("net_income", "revenue")],
            "effective_tax_rate": [
                _positive_base(t, p) for t, p in pairs("tax_provision", "pretax_income")
            ],
        }
        for key in ("revenue", "net_income", "diluted_eps"):
            out[f"{key}_growth"] = _sequential(col(key))
            if quarterly:
                out[f"{key}_growth_yoy"] = _year_on_year(col(key), dates)
        return out
    if statement == "balance":
        return {
            "current_ratio": [
                _ratio(a, lb) for a, lb in pairs("current_assets", "current_liabilities")
            ],
            "debt_to_equity": [
                _positive_base(d, e) for d, e in pairs("total_debt", "stockholders_equity")
            ],
        }
    if "free_cash_flow" not in lines:
        return {}  # no capex line (banks, insurers, REITs): no free cash flow to speak of
    return {"fcf_growth": _sequential(col("free_cash_flow"))}
