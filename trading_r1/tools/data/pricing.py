# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The shared OHLCV pricing engine behind the price tools.

One fetch and one response for all asset classes; what differs by asset class is named on each
tool: its annualization calendar (252 trading days for exchange-traded assets, 260 weekdays
for FX, 365 for 24/7 crypto), so the calendar is right by construction and the model never
picks one.

Ready-to-chain series are the primary composition surface: `closes` and `simple_returns` (with
`dates`/`periods_per_year`) feed the compute tools directly. Tauric serves one price basis per
instrument, and `closes` are that series: for listed securities it is adjusted for splits and
dividends (a total-return basis; the last bar is the traded close and earlier levels sit below
the prices that traded, by the dividends since), for indices, futures and crypto the quoted
price. `period_return` is `total` on an adjusted series and `price` otherwise. Derived measures
are standard ones only.

The asset class follows from the resolved instrument, so every symbol is annualized on its own
calendar.

Windows: a preset (1d … 10y, ytd, max) or a lookback in weeks, months or years ("2w", "18mo",
"3y"), converted to a start date so a request for three years gets three years, or in days
("10d"), the last N sessions. A window counts back from `end` when one is given, else from
today; a `start` replaces the count. Tauric serves daily bars a year at a time (six months for
indices, FX and crypto), so the engine pages back with `end`; weekly and monthly bars are
resampled from daily. Intraday bars (15m, 60m) are served for listed securities, in windows up
to 28 days, paged by the sessions one response carries. They annualize on the density the series
itself shows: trading days × the median bars per complete session. With fewer than three sessions
in the window the class's nominal session is used, ⌈session minutes ÷ bar minutes⌉.

FX: Tauric withholds the daily close of a currency pair, so an FX bar's value is its open
snapshot and the series is open-to-open.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from trading_r1.contract.spec import Provenance, Result, Series, ToolError, hint
from trading_r1.contract.units import Code, Count, Day, Fraction, Number, Price, Text
from trading_r1.tools.compute.risk.volatility import realized_vol
from trading_r1.tools.compute.series import pct_returns
from trading_r1.tools.compute.technical.indicators import Cross, last_cross, sma_last
from trading_r1.tools.data.common import is_num, months_back
from trading_r1.tools.data.symbols import Instrument, resolve
from trading_r1.tools.data.tauric import Tauric, provenance

PRESET_PERIODS = ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max")
_LOOKBACK = re.compile(r"^(\d{1,3})(d|w|mo|y)$")
_MAX_LOOKBACK_YEARS = 50  # a unit-error rail, not a data limit
_UNITS_PER_YEAR = {"d": 252, "w": 52, "mo": 12, "y": 1}  # "Nd" counts sessions
_DAYS_PER_UNIT = {"d": 1, "w": 7, "mo": 31, "y": 366}
# A calendar lookback starts at midnight, so it also spans the hours elapsed today.
_START_OF_DAY_SLACK = 1
_PRESET_DAYS = {
    "1d": 1,
    "5d": 5,
    "1mo": 31,
    "3mo": 92,
    "6mo": 183,
    "1y": 366,
    "2y": 730,
    "5y": 5 * 366,
    "10y": 10 * 366,
}
# The longest daily window one response carries, by instrument kind, and the periods it serves.
_PAGE = {"security": "1y", "future": "1y", "index": "6mo", "fx": "6mo", "crypto": "6mo"}
_PAGE_PERIODS = (("1mo", 31), ("3mo", 92), ("6mo", 183), ("1y", 366))
_MAX_PAGES = 15  # a backstop: fifteen years at a year a page


@dataclass(frozen=True)
class Intraday:
    minutes: int  # per bar
    page_sessions: int  # the most sessions one response carries


INTRADAY = {"15m": Intraday(15, 7), "60m": Intraday(60, 28)}
INTRADAY_MAX_DAYS = 28
INTERVALS = (*INTRADAY, "1d", "1wk", "1mo")
# Intraday density: sessions needed to measure bars per session (the first and last sessions of
# a window can be partial, so they are left out of the count).
_MIN_SESSIONS_FOR_DENSITY = 3
_WEEKS_PER_YEAR = 52
_MONTHS_PER_YEAR = 12
# A history that begins more than this after the requested start is reported as shorter than
# asked (a week absorbs weekends and holidays at the window's edge); likewise sessions for "Nd".
_SHORT_HISTORY_SLACK_DAYS = 7
_SHORT_HISTORY_SLACK_SESSIONS = 2


@dataclass(frozen=True)
class AssetClass:
    """What differs by asset class: the annualization calendar and which instrument kinds belong
    to it."""

    label: str
    trading_days: float  # bars per year at a 1d interval
    session_minutes: int  # nominal session, for intraday bars when density can't be measured
    kinds: frozenset[str]  # the resolver's instrument kinds this class serves
    caveat: str | None = None  # a basis note the markdown must carry
    level_digits: int = 2  # decimals when the markdown quotes a level

    def bars_per_session(self, interval: str) -> int:
        """Nominal intraday bars per full session: ⌈session ÷ bar⌉."""
        return math.ceil(self.session_minutes / INTRADAY[interval].minutes)

    def bars_per_year(self, interval: str) -> float:
        if interval in INTRADAY:
            return self.trading_days * self.bars_per_session(interval)
        return {"1d": self.trading_days, "1wk": _WEEKS_PER_YEAR, "1mo": _MONTHS_PER_YEAR}[interval]


_REGULAR_SESSION = 390  # 09:30-16:00 US equities
_ROUND_THE_CLOCK = 24 * 60  # FX (weekdays) and crypto
_GLOBEX_SESSION = 23 * 60  # CME futures: 23 hours with a one-hour daily break
EQUITY = AssetClass(
    "equity",
    252,
    _REGULAR_SESSION,
    frozenset({"security", "index"}),
)
# FX trades every weekday (no exchange holidays). Quotes are shown to the pip (4 decimals).
FX = AssetClass(
    "FX pair",
    260,
    _ROUND_THE_CLOCK,
    frozenset({"fx"}),
    caveat=(
        "Tauric withholds FX daily closes: each bar's value is its open snapshot, so returns "
        "run open to open."
    ),
    level_digits=4,
)
CRYPTO = AssetClass(
    "crypto asset",
    365,
    _ROUND_THE_CLOCK,
    frozenset({"crypto"}),
)
FUTURE = AssetClass(
    "future",  # commodities chiefly, but any continuous future (ES, 6E, ZN)
    252,
    _GLOBEX_SESSION,
    frozenset({"future"}),
    caveat="Continuous front-month future: returns across contract rolls include the roll.",
)

ASSET_CLASSES = (EQUITY, FX, CRYPTO, FUTURE)


def asset_class_for(kind: str | None) -> AssetClass | None:
    """The asset class whose price tool serves an instrument kind."""
    return next((c for c in ASSET_CLASSES if kind in c.kinds), None)


@dataclass(frozen=True)
class Bars:
    """A fetched history: the usable bars plus what Tauric says about the instrument."""

    rows: list[dict[str, Any]]
    instrument: Instrument | None = None
    currency: str | None = None
    instrument_type: str | None = None
    adjusted: bool = False  # closes adjusted for splits and dividends (a total-return basis)
    requested_start: str | None = None  # the start date a lookback asked for
    requested_sessions: int | None = None  # N, for an "Nd" window
    requested_end: str | None = None  # the last date a window ending in the past asked for
    limits: tuple[str, ...] = ()  # what Tauric states limits these bars


def validate_period(v: str) -> str:
    # a lookback: `3m` is months here, since minutes belong to `interval`
    v = re.sub(r"^(\d+)m$", r"\1mo", v.strip().lower())
    if v in PRESET_PERIODS:
        return v
    match = _LOOKBACK.match(v)
    years = int(match.group(1)) / _UNITS_PER_YEAR[match.group(2)] if match else 0
    if not 0 < years <= _MAX_LOOKBACK_YEARS:
        raise ValueError(
            f"period must be one of {PRESET_PERIODS} or a lookback like '10d', '18mo' or '3y'"
        )
    return v


def window_days(period: str, today: Any = None) -> float:
    """The calendar days a window spans (an upper bound), N for "Nd", ``inf`` for max."""
    if period == "max":
        return math.inf
    if period == "ytd":
        return _day(today).timetuple().tm_yday
    if period in _PRESET_DAYS:
        return _PRESET_DAYS[period]
    count, unit = _LOOKBACK.match(period).groups()
    slack = 0 if unit == "d" else _START_OF_DAY_SLACK
    return int(count) * _DAYS_PER_UNIT[unit] + slack


class PriceWindow(BaseModel):
    """The window and bar size shared by every price tool."""

    period: str = Field(
        "1y",
        description="Window: a preset (1d, 5d, 1mo … 10y, ytd, max) or a lookback: '2w', '18mo', "
        "'3y', or '10d' (the last 10 sessions). Counts back from `end`, or from today.",
    )
    start: dt.date | None = Field(
        None, description="First date of the window (overrides period), e.g. 2020-03-01."
    )
    end: dt.date | None = Field(
        None, description="Last date of the window, e.g. 2020-03-31 (default: the latest bar)."
    )
    interval: str = Field(
        "1d",
        description="Bar interval: 1d (default), 1wk, 1mo, or intraday 15m and 60m, for listed "
        f"securities only (not indices, FX, crypto or futures), in a window of at most "
        f"{INTRADAY_MAX_DAYS} days.",
    )

    @field_validator("period")
    @classmethod
    def _period(cls, v: str) -> str:
        return validate_period(v)

    @field_validator("interval")
    @classmethod
    def _interval(cls, v: str) -> str:
        if v not in INTERVALS:
            raise ValueError(f"interval must be one of {INTERVALS}")
        return v

    @model_validator(mode="after")
    def _window(self) -> PriceWindow:
        if self.start and self.end and self.start > self.end:
            raise ValueError(f"start {self.start} is after end {self.end}")
        if self.interval in INTRADAY and _span(self.period, self.start, self.end) > (
            INTRADAY_MAX_DAYS
        ):
            raise ValueError(
                f"{self.interval} bars cover {INTRADAY_MAX_DAYS} days at a time; ask for a "
                f"window of at most {INTRADAY_MAX_DAYS} days, or a daily interval for a longer one"
            )
        return self


def _span(period: str, start: dt.date | None, end: dt.date | None) -> float:
    """The calendar days a window spans: from `start` when it has one, else its period's."""
    return (_day(end) - start).days + 1 if start else window_days(period, end)


def _lookback(period: str) -> tuple[int | None, str | None]:
    """(count, unit) of a lookback, or (None, None) for a preset."""
    if period in PRESET_PERIODS:
        return None, None
    count, unit = _LOOKBACK.match(period).groups()
    return int(count), unit


_SESSIONS_OF_PRESET = {"1d": 1, "5d": 5}
_PRESET_OFFSET = {
    "1mo": (1, "mo"),
    "3mo": (3, "mo"),
    "6mo": (6, "mo"),
    "1y": (1, "y"),
    "2y": (2, "y"),
    "5y": (5, "y"),
    "10y": (10, "y"),
}


def _day(today: Any) -> dt.date:
    """A date from None (today), a date or an ISO string."""
    if today is None:
        return dt.date.today()
    return today if isinstance(today, dt.date) else dt.date.fromisoformat(today)


def window_start(period: str, today: Any = None) -> str | None:
    """The first date a window covers, or None when it is counted in sessions (1d, 5d, "Nd") or
    unbounded (max). Calendar arithmetic: month ends clamp, so 1mo before 31 March is 28/29
    February."""
    today = _day(today)
    if period == "ytd":
        return today.replace(month=1, day=1).isoformat()
    preset = period in PRESET_PERIODS
    count, unit = _PRESET_OFFSET.get(period, (None, None)) if preset else _lookback(period)
    if unit in (None, "d"):
        return None
    if unit == "w":
        return (today - dt.timedelta(weeks=count)).isoformat()
    return months_back(today, count * (_MONTHS_PER_YEAR if unit == "y" else 1)).isoformat()


def window_sessions(period: str) -> int | None:
    """N for a window counted in sessions (1d, 5d, "Nd"), else None."""
    if period in _SESSIONS_OF_PRESET:
        return _SESSIONS_OF_PRESET[period]
    count, unit = _lookback(period)
    return count if unit == "d" else None


def _first_period(kind: str, days: float) -> str:
    """The smallest period one response serves that covers `days`, within the kind's page."""
    page = _PAGE[kind]
    for name, span in _PAGE_PERIODS:
        if span >= days or name == page:
            return name
    return page


def _row(b: dict[str, Any], *, fx: bool, intraday: bool) -> dict[str, Any] | None:
    """A Tauric bar as an engine row, or None when it carries no usable value (a withheld
    close, a holiday). An FX bar's value is its open."""
    value = b["open"] if fx else b["close"]
    if not is_num(value) or value <= 0:
        return None
    row = {
        "date": utc_minute(b["start"]) if intraday and b.get("start") else b["date"],
        "open": b["open"],
        "high": b.get("high"),
        "low": b.get("low"),
        "close": value,
        "volume": b.get("volume"),
    }
    return row


def utc_minute(start: str) -> str:
    """An intraday bar's start as an ISO instant to the minute, with its UTC offset."""
    return dt.datetime.fromisoformat(start).astimezone(dt.UTC).isoformat(timespec="minutes")


def resample(rows: list[dict[str, Any]], interval: str) -> list[dict[str, Any]]:
    """Daily bars as weekly (ISO week) or monthly bars, dated by their last session; the newest
    group may be partial, as a live week or month is."""
    if interval == "1d":
        return rows
    import datetime as dt

    def key(r: dict[str, Any]) -> tuple[int, int]:
        d = dt.date.fromisoformat(r["date"])
        return d.isocalendar()[:2] if interval == "1wk" else (d.year, d.month)

    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    out = []
    for g in groups.values():
        volumes = [r["volume"] for r in g]
        out.append(
            {
                "date": g[-1]["date"],
                "open": g[0]["open"],
                "high": max((r["high"] for r in g if is_num(r["high"])), default=None),
                "low": min((r["low"] for r in g if is_num(r["low"])), default=None),
                "close": g[-1]["close"],
                "volume": sum(volumes) if all(is_num(v) for v in volumes) else None,
            }
        )
    return out


async def fetch_bars(
    symbol: str,
    period: str,
    interval: str,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> Bars:
    """OHLCV bars for a symbol of any asset class a price tool serves: `period` counted back from
    `end` (or today), or from `start` when one is given."""
    inst = resolve(symbol)
    if asset_class_for(inst.kind) is None:
        raise ToolError(f"no price tool serves {inst.key}")
    intraday = interval in INTRADAY
    if intraday and inst.kind != "security":
        raise ToolError(
            f"{interval} bars are served for listed securities only; {inst.key} has daily bars"
        )
    fx = inst.kind == "fx"
    first_day = start.isoformat() if start else window_start(period, end)
    sessions = None if start else window_sessions(period)
    last_day = end.isoformat() if end else None
    span = _span(period, start, end)
    async with Tauric() as api:
        if intraday:
            body, raw = await _intraday_pages(api, inst, interval, sessions or int(span), last_day)
        else:
            try:
                body, raw = await _daily_pages(api, inst, first_day, sessions, span, last_day)
            except ToolError as err:
                if inst.kind == "index" and err.code == "not_supported":
                    raise ToolError(
                        f"{inst.key} has values, not prices: data.macro.series",
                        code=err.code,
                        retry=err.retry,
                    ) from err
                raise
    instrument = body.get("instrument") or {}
    rows = [r for b in raw if (r := _row(b, fx=fx, intraday=intraday)) is not None]
    if not rows:
        raise ToolError(f"no price data for '{symbol}': check the symbol / period")
    if sessions is not None:
        days = sorted({r["date"][:10] for r in rows})[-sessions:]
        rows = [r for r in rows if r["date"][:10] >= days[0]]
    elif first_day is not None:
        latest = rows[-1]["date"]
        rows = [r for r in rows if r["date"] >= first_day]
        if not rows:
            raise ToolError(
                f"no price data for '{symbol}' since {first_day}: its latest bar is {latest}"
            )
    if not intraday:
        rows = resample(rows, interval)
    return Bars(
        rows,
        inst,
        currency=instrument.get("currency"),
        instrument_type=(instrument.get("classification") or {}).get("instrument_type"),
        adjusted=body.get("price_basis") == "split_dividend_adjusted",
        requested_start=first_day,
        requested_sessions=sessions,
        requested_end=last_day if end and end < dt.date.today() else None,
        limits=tuple(api.limits),
    )


async def _intraday_pages(
    api: Tauric, inst: Instrument, interval: str, sessions: int, end: str | None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Intraday bars for the `sessions` up to `end`, paged backwards through
    `coverage.continue_end`. Oldest first.

    The route takes a `start`/`end` window, so a session count is widened to calendar days
    (a listed security trades about 5 of 7 days; `fetch_bars` trims to the exact sessions
    afterwards). Bars are read from the current `data` container or the older `bars`."""
    page = INTRADAY[interval].page_sessions
    last = end or dt.date.today().isoformat()
    back = int(math.ceil(sessions * 7 / 5)) + 4
    start = (dt.date.fromisoformat(last) - dt.timedelta(days=back)).isoformat()
    first: dict[str, Any] = {}
    raw: list[dict[str, Any]] = []
    for served in range(0, sessions, page):
        body = await api.get(f"{inst.path}/history", interval=interval, start=start, end=end)
        first = first or body
        raw = _bars_of(body) + raw
        end = (body.get("coverage") or {}).get("continue_end")
        if not end:
            break
    return first, raw


async def _daily_pages(
    api: Tauric,
    inst: Instrument,
    start: str | None,
    sessions: int | None,
    days: float,
    end: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Daily bars for a window up to `end`, paged backwards through `coverage.continue_end` until
    the window's start (or its session count) is covered or the history ends. Oldest first.

    The history route takes a concrete `start`/`end` window (at most 366 days a call) and pages
    back by `end`; a `period` preset is turned into dates here. A window counted in sessions is
    widened to calendar days (sessions ≈ 5 of 7 days for exchange assets, every day for the
    round-the-clock classes); `fetch_bars` trims the last `sessions` sessions afterwards, so
    over-serving is safe."""
    last = end or dt.date.today().isoformat()
    if start is None and sessions is not None:
        back = int(math.ceil(sessions * 7 / 5)) + 4
        start = (dt.date.fromisoformat(last) - dt.timedelta(days=back)).isoformat()
    bars: dict[str, dict[str, Any]] = {}
    first: dict[str, Any] = {}
    for _ in range(_MAX_PAGES):
        body = await api.get(
            f"{inst.path}/history",
            start=start if not first else None,
            end=end,
        )
        first = first or body
        for b in _bars_of(body):
            bars.setdefault(b["date"], b)
        end = (body.get("coverage") or {}).get("continue_end")
        usable = sum(
            _row(b, fx=inst.kind == "fx", intraday=False) is not None for b in bars.values()
        )
        # `continue_end` is the day before the page's first date: a start on or after it is covered
        if not end or not _bars_of(body) or (start is not None and end < start):
            break
        if sessions is not None and usable >= sessions:
            break
    return first, [bars[d] for d in sorted(bars)]


def _bars_of(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The bars a history answer carries: the current `data`, or the older `bars` shape."""
    return body.get("data") or body.get("bars") or []


def annualization(
    asset: AssetClass, interval: str, stamps: list[str]
) -> tuple[float, float | None]:
    """(bars per year, bars per session): intraday from the series' observed density (the
    class's nominal session when too few sessions to measure), else the class calendar."""
    if interval not in INTRADAY:
        return asset.bars_per_year(interval), None
    observed = session_density(stamps)
    per_session = observed if observed is not None else asset.bars_per_session(interval)
    return asset.trading_days * per_session, per_session


class Bar(BaseModel):
    date: Day
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Count


class RealizedVol(BaseModel):
    per_bar: Fraction
    annualized: Fraction
    periods_per_year: Number


# Trailing windows as a terminal quotes them: the previous bar, then a week, a month, a quarter,
# half a year and a year back on the calendar.
_TRAILING = (("1d", None), ("1w", 0), ("1m", 1), ("3m", 3), ("6m", 6), ("1y", 12))


class Trailing(BaseModel):
    """The return from the last close on or before `from_date` to the latest close."""

    window: Code
    from_date: Day
    ret: Fraction


def trailing_returns(dates: list[str], closes: list[float]) -> list[Trailing]:
    """The windows the history reaches back to, each ending on the latest close; a window starts on
    the last bar on or before the date that far back (a week is seven days: Friday to Friday)."""
    last = dt.date.fromisoformat(dates[-1][:10])
    days = [d[:10] for d in dates]
    out: list[Trailing] = []
    for name, months in _TRAILING:
        if months is None:
            i = len(closes) - 2
        else:
            target = last - dt.timedelta(days=7) if months == 0 else months_back(last, months)
            if days[0] > target.isoformat():
                continue
            i = max(k for k, d in enumerate(days) if d <= target.isoformat())
        if i >= 0 and closes[i]:
            out.append(Trailing(window=name, from_date=dates[i], ret=closes[-1] / closes[i] - 1))
    return out


class PriceOutput(Series):
    """A price history: the summary, then `closes` aligned to `dates`; `simple_returns`
    aligns to `dates[1:]`; `bars` holds the full OHLCV rows."""

    ticker: Code
    key: Code = None
    asset_class: Code
    instrument_type: Code = None
    currency: Code = None
    price_basis: Code
    notes: list[Text] = []
    n_bars: Count
    start_date: Day
    start: Price
    current: Price
    period_return: dict[str, Fraction]
    trailing: list[Trailing] = []
    high: Price
    high_date: Day
    low: Price
    low_date: Day
    sma: dict[str, Price]
    last_cross: Cross | None = None
    realized_vol: RealizedVol
    periods_per_year: Number
    bars_per_session: Number = None
    dates: list[Day]
    closes: list[Price]
    simple_returns: Annotated[list[Fraction], hint(hidden=True)]
    bars: Annotated[list[Bar], hint(hidden=True)]


def price_response(symbol: str, bars: Bars, interval: str, asset: AssetClass) -> Result:
    rows = bars.rows
    intraday = interval in INTRADAY
    income = bars.adjusted and not intraday
    closes = [r["close"] for r in rows]
    quoted = closes  # one price basis per instrument: levels and returns share it
    bars_per_year, per_session = annualization(asset, interval, [r["date"] for r in rows])
    per_bar, annualized = realized_vol(closes, bars_per_year)
    hi = max(range(len(quoted)), key=quoted.__getitem__)
    lo = min(range(len(quoted)), key=quoted.__getitem__)
    past = bars.requested_end is not None
    notes = [
        "Levels and returns use the quoted close."
        if not income
        else "Closes are adjusted for splits and dividends (total-return basis): the levels in a "
        "past window sit below the prices that traded then, by the dividends since; returns over "
        "the window are right."
        if past
        else "Closes are adjusted for splits and dividends (total-return basis): the last bar is "
        "the traded close, earlier levels sit below the prices that traded."
    ]
    if past:
        notes.append(
            f"The window ends {rows[-1]['date']}: `current` and `trailing` are as of that bar."
        )
    if bars.instrument and bars.instrument.note:
        notes.append(bars.instrument.note + ".")
    if asset.caveat:
        notes.append(asset.caveat)
    if bars.instrument_type == "rate":
        notes.append(f"{symbol} is a yield in percent: returns are relative changes in the yield.")
    if _still_forming(rows[-1]["date"], interval):
        notes.append(f"The last bar ({rows[-1]['date']}) is still forming.")
    notes.extend(bars.limits)
    sessions = len({r["date"][:10] for r in rows})
    wanted = bars.requested_sessions
    if wanted is not None and sessions < wanted - _SHORT_HISTORY_SLACK_SESSIONS:
        notes.append(f"Only {sessions} sessions returned of the {wanted} requested.")
    if _starts_late(bars.requested_start, rows[0]["date"]):
        notes.append(
            f"History begins {rows[0]['date']}, after the requested start {bars.requested_start}."
        )
    out = PriceOutput(
        ticker=symbol,
        key=bars.instrument.key if bars.instrument else None,
        asset_class=asset.label,
        instrument_type=bars.instrument_type,
        currency=bars.currency,
        price_basis="split_dividend_adjusted" if bars.adjusted else "quoted",
        notes=notes,
        n_bars=len(rows),
        start_date=rows[0]["date"],
        start=quoted[0],
        current=quoted[-1],
        period_return={("total" if income else "price"): closes[-1] / closes[0] - 1},
        trailing=[] if intraday else trailing_returns([r["date"] for r in rows], closes),
        high=quoted[hi],
        high_date=rows[hi]["date"],
        low=quoted[lo],
        low_date=rows[lo]["date"],
        sma={str(w): sma_last(quoted, w) for w in (20, 50, 200)},
        last_cross=last_cross(quoted),
        realized_vol=RealizedVol(
            per_bar=per_bar, annualized=annualized, periods_per_year=bars_per_year
        ),
        # closes feed price-taking tools, simple_returns return-taking ones; periods_per_year
        # annualizes (intraday: trading days × bars_per_session)
        periods_per_year=bars_per_year,
        bars_per_session=per_session,
        dates=[r["date"] for r in rows],
        closes=closes,
        simple_returns=pct_returns(closes),
        bars=rows,
    )
    return Result(out, Provenance(**provenance(subject=symbol, as_of=rows[-1]["date"])))


def _starts_late(requested: str | None, first: str) -> bool:
    if requested is None:
        return False
    first_day = dt.date.fromisoformat(first[:10])  # intraday stamps carry a time and an offset
    return (first_day - dt.date.fromisoformat(requested)).days > _SHORT_HISTORY_SLACK_DAYS


def session_density(stamps: list[str]) -> float | None:
    """Median bars per complete session in an intraday series (sessions keyed by the exchange's
    local date; the first and last are left out as possibly partial), or None when there are
    too few sessions to tell."""
    import statistics

    counts: dict[str, int] = {}
    for stamp in stamps:
        counts[stamp[:10]] = counts.get(stamp[:10], 0) + 1
    if len(counts) < _MIN_SESSIONS_FOR_DENSITY:
        return None
    return float(statistics.median(list(counts.values())[1:-1]))


def _still_forming(stamp: str, interval: str) -> bool:
    """True when an intraday bar's interval has not yet closed."""
    if interval not in INTRADAY:
        return False
    end = dt.datetime.fromisoformat(stamp) + dt.timedelta(minutes=INTRADAY[interval].minutes)
    return end > dt.datetime.now(end.tzinfo)
