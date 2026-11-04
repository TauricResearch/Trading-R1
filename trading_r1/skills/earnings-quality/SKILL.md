---
name: earnings-quality
description: "Assess the quality of reported earnings: the accrual/cash-flow gap from the figures the tools give, plus the non-GAAP bridge and forensic screens sourced from filings, read as red-flag direction, with contested thresholds and diagnostics flagged, never asserted as verdicts."
tags: [earnings-quality, accruals, forensic, non-gaap, fundamentals]
domain: equity
requires_tools:
  - data_equity_reference
  - data_equity_fundamentals
---

# Earnings quality

Are reported earnings backed by cash and sustainable, or flattered by accruals and
adjustments? Read the **direction** of the red flags; treat forensic scores as diagnostics,
not verdicts, and do not assert contested thresholds.

## Workflow

1. **Anchor.** `data_equity_reference` and `data_equity_fundamentals`: the TTM summary,
   then statement income and statement cashflow (annual, about four years) for net income,
   operating cash flow, stock comp, capex and free cash flow by
   year (working capital from the balance sheet: receivables, inventory, payables), and statement balance for receivables, inventory, total assets and liabilities.
2. **The accrual test.** Compare TTM net income with TTM operating cash flow: a large gap
   where earnings exceed cash generation is the classic warning. The **accrual component of
   earnings is less persistent** than the cash component (high-accrual firms tend to
   underperform), though the effect is a diagnostic lens, not a live return signal (the
   published anomaly has weakened). Run it year by year on the statements, not only on the
   TTM figures.
3. **Cash conversion.** Is OCF/NI stable and near or above 1? (The ratio is unstable or
   meaningless when net income is near zero or negative; read it with the levels, not as a
   bare threshold.) A deteriorating trend, or working-capital builds funding reported
   growth, is a flag: receivables and inventory against revenue from the balance and
   income statements show it (days need revenue and cost of revenue from the same periods).
4. **The non-GAAP bridge.** `search` + `visit` the latest release: what is excluded to get
   from GAAP to "adjusted"? Recurring "one-time" charges and a widening GAAP-to-non-GAAP gap
   are concerns; **stock-comp is a real economic cost**: an add-back that flatters adjusted
   figures, not a legitimate exclusion.
5. **Forensic screens.** The Beneish (manipulation) and Altman (distress) models take
   balance-sheet and income inputs the statements provide for most lines; **inputs they
   lack (e.g. retained earnings, SG&A detail for some filers) come from the filings; cite
   them, state which threshold you used, and treat the score as one diagnostic among
   several**, never a verdict. Both thresholds are contested.
6. **Synthesize.** Is earnings backed by cash, are the adjustments legitimate, and what are
   the specific flags, each with its direction, its source, and your confidence.

## Principles

- **Cash flow is generally harder to sustain fraudulently than earnings**, but it is still
  affected by classification choices, working-capital timing, and acquisitions; anchor in
  the net-income-to-cash divergence, don't treat cash as unfakeable.
- **The accrual component is less persistent**, not mechanically self-reversing: the
  direction is robust, the magnitude and timing are not.
- **Recurring "one-time" charges are operating costs**, whatever they are labelled; SBC is a
  real cost.
- **Beneish/Altman thresholds are contested and their inputs aren't in the data tools**:
  run them only on sourced inputs, report the cutoff used, and never present a score as a
  confident verdict.
- **Deleting outliers is not a fix**: in forensic work the outlier is usually the
  phenomenon of interest.
