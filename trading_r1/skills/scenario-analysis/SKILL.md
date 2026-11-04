---
name: scenario-analysis
description: "Value an equity under several coherent worldviews: bull, base and bear (or custom) cases as correlated bundles of assumptions, a DCF under each, and the intrinsic-value range across them versus the price, with what would falsify each case."
tags: [equity, valuation, scenario, dcf, bull-bear]
domain: valuation
requires_tools:
  - data_equity_reference
  - data_equity_fundamentals
  - data_equity_estimates
  - data_market_rates
  - compute_valuation_wacc
  - compute_modeling_three_statement
  - compute_valuation_dcf
  - compute_modeling_sensitivity
  - compute_valuation_dcf_sensitivity
---

# Scenario analysis

A valuation *range* across stated worldviews, not a point estimate and not a forecast. Use it
for "bull/base/bear DCF", "what is it worth if <driver> changes", or any question about
coherent alternatives. For one input varied with the rest held fixed, a sensitivity table
(`compute_valuation_dcf_sensitivity` or `compute_modeling_sensitivity`) is the tool instead.

## Workflow

1. **Set the base.** `data_equity_reference` (price, shares, beta), `data_equity_fundamentals`
   with statement income and cashflow (revenue, margins, free cash flow by year), and
   `data_equity_estimates` (consensus growth). The base case anchors on these.
2. **Name the load-bearing assumptions**: the two to four inputs that move *this* company's
   value (revenue growth, margin path, reinvestment, terminal growth, discount rate). Choosing
   them is the analysis.
3. **Define the cases as bundles.** Each case is a worldview whose assumptions move together for
   one reason (stronger demand -> faster growth *and* wider margins). The optimistic end of every
   input chosen independently is a tail, not a scenario. Mark each assumption measured (from the
   data) or assumed (the case's judgment).
4. **Run each case.** Cash flows: `compute_modeling_three_statement` with the case's drivers
   (see `three-statement-model`) gives its `fcff_schedule`. Discount rate: `data_market_rates` for
   the risk-free rate, then `compute_valuation_wacc` (vary it only where the worldview implies
   it). Value with `compute_valuation_dcf`: the case's FCFF (`prev:<id>.fcff_schedule`) at the
   WACC, less net debt (`prev:<id>.ttm.net_debt` from the fundamentals summary), over shares
   outstanding (reference). Keep the horizon and terminal method the same across cases, so the
   comparison reflects the worldviews, not the model.
5. **Synthesize.** The per-share value in each case against the price, the spread across cases,
   the assumption that swings the result most, and what would falsify each case.

## Answer shape

A case table (each assumption's value per case, then value per share and the implied move
from the price), the range and its dominant driver, the thesis behind each case in a sentence,
and each case's falsifier ("bear breaks if margins hold above X").

## Principles

- Each case needs its own consistent set of assumptions.
- Hold the model structure constant; vary the assumptions.
- A scenario range is not a forecast or a probability-weighted value; the reader chooses the
  worldview.
- Carry tool numbers at the tool's precision; say which inputs are judgment.
