# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Symbols to Tauric instruments.

Tauric names instruments by kind and key (`security:AAPL`, `future:XNYM:CL`, `fx:EURUSD`,
`crypto:BTC-USDT`, `series:us.equity_index.sp500`) and serves each kind from its own route. The
tools accept the spellings people and models use (`AAPL`, `XNAS:AAPL`, `CL=F`, `EURUSD=X`,
`BTC-USD`, `^GSPC`, `BRK-B`) and the Tauric keys themselves, and resolve them here: a closed set
of conventions, not a lookup of every name; anything else goes to the data API as given, whose
refusals already name the right key. `data.market.symbol_search` finds the symbol for a name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from trading_r1.contract.spec import ToolError

# The headline indices and Treasury yield indices people write with a caret; every other priced
# series is its series key.
INDEX_SERIES = {
    "^GSPC": "us.equity_index.sp500",
    "^SPX": "us.equity_index.sp500",
    "^DJI": "us.equity_index.djia",
    "^IXIC": "us.equity_index.nasdaq_composite",
    "^RUT": "us.equity_index.russell_2000",
    "^VIX": "us.volatility_index.vix",
    "^IRX": "us.treasury.yield_index.13w",
    "^FVX": "us.treasury.yield_index.5y",
    "^TNX": "us.treasury.yield_index.10y",
    "^TYX": "us.treasury.yield_index.30y",
}
_CRYPTO = re.compile(r"^([A-Z0-9]{2,10})[-/](USDT|USDC|USD)$")
_CRYPTO_BARE = re.compile(r"^([A-Z0-9]{2,10})(USDT|USDC|USD)$")
_SHARE_CLASS = re.compile(r"^([A-Z]{1,5})-([A-Z])$")
_SAFE = re.compile(r"^[A-Za-z0-9.:/_=^-]+$")
_TICKER = re.compile(r"^(?:[A-Z0-9]{4}:)?[A-Z0-9][A-Z0-9.-]*$")  # a symbol, or MIC:SYMBOL
_ROOT = re.compile(r"^[A-Z0-9]+$")
_FX = re.compile(r"^([A-Z]{3})[-/_]?([A-Z]{3})$")
# The currencies Tauric serves pairs of: a bare six-letter pair of two of them is FX.
_CURRENCIES = frozenset({"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"})
_BARE_BTC = re.compile(r"^(BTC)(USDT|USD)$")


@dataclass(frozen=True)
class Instrument:
    """A resolved instrument: its kind, Tauric key, route and the note the user is owed."""

    kind: str  # security | index | future | fx | crypto
    key: str
    path: str  # under /v1
    label: str  # the symbol as the tools show it
    note: str | None = None


def canonical(name: str) -> str:
    """One spelling of an instrument's name, as the data API takes it: a series key in lower
    case; anything else stripped and upper-cased, without the `security:` prefix, a share class
    with its dot (`BRK-B` is `BRK.B`)."""
    s = name.strip().upper().removeprefix("SECURITY:")
    if s.startswith("SERIES:"):
        return s.lower()
    mic, colon, ticker = s.rpartition(":")  # a listing: the share class is in its ticker
    if m := _SHARE_CLASS.match(ticker):
        ticker = f"{m.group(1)}.{m.group(2)}"
    return f"{mic}{colon}{ticker}"


def _shaped(name: str, pattern: re.Pattern[str]) -> str:
    """A name safe to put in a route, else a refusal that says what a symbol looks like."""
    if not pattern.match(name):
        raise ToolError(f"'{name}' is not a symbol: use a ticker, CL=F, EURUSD=X, BTC-USD or ^GSPC")
    return name


def _security(ticker: str) -> Instrument:
    _shaped(ticker, _TICKER)
    return Instrument("security", f"security:{ticker}", f"/securities/{ticker}", ticker)


def _index(series: str, label: str) -> Instrument:
    return Instrument("index", f"series:{series}", f"/series/{series}", label)


def _future(root: str) -> Instrument:
    _shaped(root, _ROOT)
    return Instrument(
        "future", f"future:{root}/continuous", f"/futures/{root}/continuous", f"{root}=F"
    )


def _fx(pair: str) -> Instrument:
    return Instrument("fx", f"fx:{pair}", f"/fx/{pair}", pair)


def _crypto(base: str, quote: str) -> Instrument:
    """Tauric quotes crypto in stablecoins: a USD pair is served as its USDT pair."""
    served = "USDT" if quote == "USD" else quote
    pair = f"{base}-{served}"
    note = f"{base}-USD is served as {pair}: quoted in USDT, not USD" if quote == "USD" else None
    return Instrument("crypto", f"crypto:{pair}", f"/crypto/pairs/{pair}", pair, note)


def _is_pair(base: str, quote: str) -> bool:
    return base != quote and {base, quote} <= _CURRENCIES


def _keyed(symbol: str) -> Instrument | None:
    """A Tauric key given as such."""
    kind, _, rest = symbol.partition(":")
    kind = kind.lower()
    if kind == "security" and rest:
        return _security(rest.upper())
    if kind == "series" and rest:
        return _index(rest.lower(), symbol)
    if kind == "future" and rest:
        return _future(rest.upper().removesuffix("/CONTINUOUS").rsplit(":", 1)[-1])
    if kind == "fx" and rest:
        return _fx(rest.upper())
    if kind == "crypto" and rest:
        base, _, quote = rest.upper().partition("-")
        return _crypto(base, quote or "USDT")
    return None


def resolve(symbol: str, hint: str | None = None) -> Instrument:
    """The instrument a symbol names. `hint` is the kind the calling tool serves ("fx",
    "crypto", "future", "security"), which settles spellings that are ambiguous alone
    (`EURUSD` is a pair to the FX tool and a ticker shape to a stock tool). Without a hint, a
    bare pair of two of the eight currencies is FX and `BTCUSD`/`BTCUSDT` is crypto; futures
    need `=F`."""
    if not _SAFE.match(symbol.strip()):
        raise ToolError(
            f"'{symbol}' is not a symbol: use a ticker, CL=F, EURUSD=X, BTC-USD or ^GSPC"
        )
    if (keyed := _keyed(symbol.strip())) is not None:
        return keyed
    s = symbol.strip().upper()
    if s.startswith("^"):
        if s in INDEX_SERIES:
            return _index(INDEX_SERIES[s], s)
        raise ToolError(
            f"'{s}' is not a headline index here: data.market.symbol_search finds the series "
            "key (series:…) for an index"
        )
    if s.endswith("=F"):
        return _future(s.removesuffix("=F"))
    if s.endswith("=X"):
        pair = s.removesuffix("=X")
        return _fx(pair if len(pair) == 6 else f"{pair}USD")
    if hint == "future":
        return _future(s)
    if (m := _FX.match(s)) and (hint == "fx" or (hint is None and _is_pair(*m.groups()))):
        return _fx(m.group(1) + m.group(2))
    if hint is None and (m := _BARE_BTC.match(s)):
        return _crypto(m.group(1), m.group(2))
    if (m := _CRYPTO.match(s)) or (hint == "crypto" and (m := _CRYPTO_BARE.match(s))):
        return _crypto(m.group(1), m.group(2))
    if hint == "crypto" and "-" not in s and "/" not in s:
        return _crypto(s, "USDT")
    if hint == "crypto":
        base, _, quote = s.replace("/", "-").partition("-")
        return _crypto(base, quote)
    return _security(canonical(s))
