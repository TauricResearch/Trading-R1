---
name: macro-briefing
description: "Brief the US macro and market backdrop: growth, labour, inflation and expectations, policy and yields, credit and stress, volatility and positioning, from the published series, each reading dated and set against the month and the year before, then what the day's news adds."
tags: [macro, economics, inflation, rates, credit, volatility, positioning]
domain: macro
requires_tools:
  - data_macro_snapshot
  - data_macro_series
  - data_market_rates
  - data_market_vol_term_structure
  - data_market_positioning
  - data_market_digest
---

# Macro briefing

Where the economy and markets stand, read from the published data, for "how is the economy
doing?", "is inflation coming down?", "what is the market pricing?" or the backdrop to a
sector or asset view.

## Workflow

1. **The dashboard.** `data_macro_snapshot` for the categories the question touches: Growth &
   Output, Employment & Labor, Inflation, Fed Policy & Rates, Credit & Stress, and Consumer &
   Income or Housing & Business when relevant. Note each reading's period: monthly data runs
   weeks behind, quarterly GDP months behind.
2. **The trends that matter.** `data_macro_series` on the few series the answer turns on (core
   CPI or core PCE, payrolls and unemployment, the high-yield spread), with enough history to
   show direction: two to five years for monthly series. Find keys with `query` or `category`.
3. **Rates and expectations.** `data_market_rates` for the Treasury curve and its slope; the 5-
   and 10-year breakevens (`data_macro_series`) for inflation expectations against realized
   inflation; fed funds and SOFR for where policy sits.
4. **Risk appetite.** `data_market_vol_term_structure` for the VIX curve (contango or
   backwardation); credit spreads and the financial-stress indices from step 1; for a
   commodity or index future in question, `data_market_positioning` (managed money or
   leveraged funds, and where their net sits in its range).
5. **What the news adds.** `data_market_digest` for the day's themes; its claims come with
   their articles as numbered sources; cite those numbers, and `visit` an article for any
   specific beyond its summary.

## Answer shape

A short verdict, then one paragraph or table per area (each figure with its period, its change
on the prior period and on the year before) and the tensions between them (hot inflation with a
softening labour market; tight spreads with an inverted curve). Close with what to watch and
when the next readings are due, if the sources state it.

## Principles

- Every figure carries its date: the latest reading of a monthly series is often six weeks old.
- Rates change in percentage points; levels by percent. Say which.
- Read direction from several observations, not one print; a single month is noise.
- Keep the publisher's figures; derive with `calc` only what the tools do not give.
- The digest is machine-written: cite its articles by number, never the digest's wording as
  a fact of its own.
