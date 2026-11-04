---
name: equity-deep-dive
description: "A thorough single-equity deep dive: read the recent qualitative picture from the web, then check it against fundamentals and health ratios, an intrinsic DCF with a sensitivity range, a comparable-company cross-check, and the analyst-consensus view."
tags: [equity, deep-dive, valuation, dcf, comps, consensus]
domain: equity
requires_tools:
  - data_equity_reference
  - data_equity_fundamentals
  - data_equity_ratios
  - data_market_rates
  - compute_valuation_wacc
  - compute_valuation_dcf
  - compute_valuation_dcf_sensitivity
  - data_equity_comps
  - data_equity_estimates
  - data_equity_earnings
  - data_equity_digest
  - data_equity_filings
---

# Equity deep dive

The full work-up on one name: the story, then intrinsic value, a relative cross-check and
the consensus view. Every claim has a source or a tool result.

## Workflow

1. **Read the picture.** `data_equity_digest` view thesis for the running synthesis (drivers,
   risks, catalysts, open questions) as a map of the debate; `data_equity_filings` for the
   latest 10-Q and results 8-K; then `search` + `visit` recent, authoritative coverage: the
   latest results/guidance, strategy, and the live debate (bull vs bear). Ground each
   qualitative claim in a page you read; this frames what the numbers must explain.
2. **Anchor.** `data_equity_reference` (identity, sector, shares, beta, price) and
   `data_equity_fundamentals` (revenue, FCF, margins, growth, cash/debt).
3. **Health.** `data_equity_ratios` for liquidity, leverage, returns, and the
   company's own multiples.
4. **Intrinsic value.** `data_market_rates` for the risk-free rate ->
   `compute_valuation_wacc` -> project an explicit FCF schedule -> `compute_valuation_dcf`,
   then `compute_valuation_dcf_sensitivity` for a bear/base/bull range.
5. **Relative cross-check.** `data_equity_comps` against a peer group you choose:
   does the relative read agree with the DCF?
6. **Consensus.** `data_equity_estimates` for price targets, forward EPS and the
   rating spread; `data_equity_earnings` for the reported record and the next report. Which
   way estimates are moving comes from the web, not the tools.
7. **Synthesize.** A view that ties the qualitative story to intrinsic value,
   relative value, and consensus, with the discount rate, key assumptions, the
   valuation range, and the main risks stated.

## Principles

- Read before you value: the web step sets the narrative the numbers must support;
  attribute web specifics to the page you read, mark anything unconfirmed as such.
- Triangulate: when the DCF, comps, and consensus disagree, report the disagreement; do not average it
  away.
- Every figure from a tool; run the math through the tools so it stays exact and
  auditable. Mark forecasts and analyst estimates as expectations, not facts. State how much value rests on the
  terminal value.
