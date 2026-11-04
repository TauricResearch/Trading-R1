---
name: attribution-analysis
description: "Explain why a portfolio beat or lagged its benchmark: Brinson-Fachler attribution into sector allocation, selection within sectors and interaction, from the benchmark's sector weights and the sectors' returns, reconciled to the active return."
tags: [portfolio, attribution, performance, brinson]
domain: portfolio
requires_tools:
  - data_market_sectors
  - data_market_prices
  - data_equity_reference
---

# Performance attribution (Brinson-Fachler)

For "was it my sector bets or my stock picks?": a portfolio, a benchmark, returns for both over
one period, and the question of where the active return came from.

## Workflow

1. **Benchmark sector weights** `wb_i`: the benchmark's weights by GICS sector. The data tools
   carry no fund holdings: take them from the user, or from the index provider's or fund
   sponsor's published sector breakdown (`search` + `visit`), and cite the page and its date,
   ideally at the period start; say if it is a later snapshot. The weights sum to 1.
2. **Benchmark sector returns** `rb_i`: `data_market_sectors` over the period (SPDR sector ETF
   total returns over their shared dates); its `benchmark.return` is the S&P 500's actual
   return.
3. **Portfolio sector weights and returns** `wp_i`, `rp_i`: from the user's holdings. Classify
   each stock with `data_equity_reference` (its sector is GICS, as Tauric gives it); a sector
   ETF holding is its sector (the `data_market_sectors` list). Take each holding's return from
   `data_market_prices` over the same window and weight it within its sector; a sector held
   passively takes its ETF's return; state it. Where the portfolio holds none of a sector,
   rp_i = rb_i by convention.
4. **Decompose** (with `calc`; rb = Σ wb_i*rb_i):
   - allocation_i = (wp_i − wb_i)*(rb_i − rb);
   - selection_i = wb_i*(rp_i − rb_i);
   - interaction_i = (wp_i − wb_i)*(rp_i − rb_i).
5. **Reconcile.** Allocation + selection + interaction equals Σwp_i*rp_i − Σwb_i*rb_i by
   construction. Report the benchmark's actual return beside Σwb_i*rb_i: the gap (sector schemes,
   snapshot weights) is a residual to state, not an input error to chase.

## Answer shape

The active return; a per-sector table (wp, wb, rp, rb and the three effects); the totals and
the residual against the benchmark's actual return; the largest contributors. State at the figures they affect: sector mapping, snapshot weights,
proxies, the period, single-period.

## Principles

- Weights and returns from the tools, not memory; the same window for every return.
- The decomposition is standard arithmetic; the residual against the real benchmark is
  reported, not hidden.
- Single-period and additive: linking periods geometrically is out of scope; say so if the
  horizon spans rebalances.
- The effects describe what happened; they are not a verdict on skill.
