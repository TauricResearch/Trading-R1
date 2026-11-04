---
name: credit-analysis
description: "Assess an issuer's creditworthiness downside-first: business risk vs financial risk, the leverage and liquidity the data tools provide, and the coverage, maturity-wall and covenant picture sourced from filings, without asserting rating-agency thresholds or figures the tools do not supply."
tags: [credit, fixed-income, leverage, coverage, solvency, fundamentals]
domain: fixed_income
requires_tools:
  - data_equity_reference
  - data_equity_fundamentals
  - data_equity_ratios
  - compute_fixed_income_credit_ratios
  - data_equity_filings
  - data_macro_series
---

# Credit analysis

Will the issuer repay, in full and on time, and what is recovered if not? The payoff is
**asymmetric** (capped upside, large downside), so the analysis is **downside-first**.
Separate business risk from financial risk, and base the read on cash flow.

## The Four Cs

**Capacity** (cash to service the debt), **Capital** (cushion beneath the debt: leverage,
equity value, subordination), **Collateral** (what backs the claim), **Covenants** (the
protections). A fifth **C: Character** (willingness to pay, track record with creditors)
matters most in family- and sponsor-owned credits.

## Workflow

1. **Business risk.** `search` + `visit` for the issuer's industry position, cyclicality
   and competitive standing. A cyclical, undifferentiated business supports far less
   leverage than a stable, advantaged one; this sets the tolerance before any ratio.
2. **Anchor the financials.** `data_equity_reference` (identity, sector, size) and
   `data_equity_fundamentals` (revenue, EBITDA, free & operating cash flow, total cash and
   debt).
3. **Capital: leverage the tools give.** `data_equity_ratios` for net-debt/EBITDA and
   debt/equity and the current ratio; `data_equity_fundamentals` for cash and total debt.
   These are the sourced leverage and liquidity figures.
4. **Capacity: coverage.** `data_equity_fundamentals` with statement income (EBITDA with its
   basis, operating income as the EBIT, interest expense, tax),
   statement cashflow (operating cash flow, capex, free cash flow) and statement balance (total debt, cash, equity, current assets
   and liabilities, at the same period ends as the flows). Feed each year to
   `compute_fixed_income_credit_ratios` for EBITDA and EBIT interest coverage, (EBITDA −
   capex)/interest, FFO/debt and FCF/debt. Use annual flows (or four quarters summed) for
   anything scaled by debt: one quarter's flows understate it about fourfold; quarterly
   data suits coverage ratios only. For banks and insurers interest expense is
   deposit and policy interest, so this panel does not apply to them. Interest expense is
   sometimes not reported to the tools: then **source it from filings via `search`/`visit`** (10-K/10-Q) and
   cite it, or say it is missing; do not infer coverage from EBITDA alone. DSCR needs
   scheduled principal, which only the filings give. Judge coverage **through a
   downturn**, not at peak margins.
5. **The questions that actually decide default** (source from filings, don't fabricate;
   `data_equity_filings` lists the 10-K, 10-Qs and 8-Ks with links to `visit`):
   the **maturity wall** (most defaults are refinancing failures, not missed coupons),
   liquidity sources vs uses over 12-24 months, **covenant headroom under a downside case**,
   fixed-vs-floating mix, and how far EBITDA falls in a downturn.
6. **The market's price of credit.** `data_macro_series` for the corporate option-adjusted
   spread of the issuer's rating bucket (`category` Credit & Stress, or `query` 'spread') and
   its history: where today's spread sits against its own range frames how much risk the
   market charges.
7. **The credit read.** Capacity and willingness to pay, the key vulnerabilities, and
   relative credit quality vs peers and the issuer's own history, expressed as relative
   quality, **not a fabricated rating** and not a numeric threshold verdict.

## Principles

- **Business risk and financial risk are separate axes**: the same leverage can be
  comfortable for one issuer and distressed for another; state the business-risk reason
  rather than asserting a rating from leverage alone.
- **Capacity is a through-the-cycle question**, and **cash-flow-based coverage (FFO/debt,
  FCF/debt, DSCR) beats EBITDA multiples**: EBITDA is not cash.
- **Rating-agency leverage anchors, recovery rates and transition matrices move**: never
  assert a numeric cutoff; point at the current published study and compare to peers.
- **Cash flow is generally harder to sustain fraudulently than earnings**, but it is still
  affected by classification, working-capital timing, acquisitions and SBC; anchor in
  FCF/OCF and watch a widening net-income-to-cash gap.
- **Where the data tools cannot ground a figure, source it from filings and cite it, or
  flag the gap**: report the missing input and do not invent a coverage ratio.
