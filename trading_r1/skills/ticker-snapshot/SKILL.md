---
name: ticker-snapshot
description: "Quick structured overview of a single public equity: identity, sector, market cap, valuation ratios, current price, recent trend."
tags: [equity, snapshot]
domain: equity
requires_tools:
  - data_equity_reference
  - data_market_prices
---

# Ticker snapshot

Give a short overview of one equity, from the tools.

## Workflow

1. Pull `data_equity_reference` for identity, sector/industry, market cap, beta,
   current price, 52-week range, and P/E.
2. Pull `data_market_prices` (2y, so the 200-day average has a year in which to cross) for the
   recent trend: SMA(20/50/200), any golden/death cross, realized volatility.
3. Write a short snapshot: what the company is, its scale and classification,
   where the price sits in its range, and the recent technical picture.

## Principles

- Report the numbers and standard indicators the tools return (SMA, golden/death
  cross). Note when a field is unavailable rather than guessing.
- Keep it to the essentials a reader needs to orient on the name quickly.
