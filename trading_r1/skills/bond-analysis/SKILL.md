---
name: bond-analysis
description: "Analyze a fixed-rate bond from its terms: price and yield, duration/convexity and DV01, and the price response to rate moves, on the right conventions, sourcing any market spread or credit context from research rather than asserting it."
tags: [fixed-income, bond, duration, convexity, credit, yield]
domain: fixed_income
requires_tools:
  - data_market_rates
  - compute_fixed_income_bond
  - compute_fixed_income_horizon_return
  - compute_fixed_income_yield_curve
---

# Bond analysis

Price it, measure its rate risk, read its yield on the right conventions. The analytic tool
values an **option-free fixed-rate** bond from the terms you give it; market spreads and
credit context are sourced, not computed here.

## Workflow

1. **State the bond.** Coupon, frequency, maturity, face, and whether it is option-free:
   the tool assumes it is. Note callable/putable/MBS/floating features up front; they change
   which duration is valid and mean the tool's price/yield is only indicative.
2. **Reference rates.** `data_market_rates` for the Treasury par curve (1m-30y, the
   10y−2y and 10y−3m spreads; a past `date` or `days` of history when the question is about
   a move). These are par yields: for a spot curve, forwards or the yield at the bond's exact
   maturity, chain `prev:<id>.curve` into `compute_fixed_income_yield_curve`.
3. **Price and yield.** `compute_fixed_income_bond` from the coupon terms + either the YTM
   or the price (it solves the other) -> the price (a **clean price approximation**: the tool
   has no settlement date, day count or accrued interest), current yield, and the
   premium/par/discount read.
4. **Rate risk.** From the same tool: Macaulay & modified duration, convexity, DV01, and the
   ±100bp price change showing the positive-convexity asymmetry. Use DV01 for hedging/sizing
   (additive), modified duration to communicate percentage sensitivity.
5. **Horizon return** (what holding it would earn): `compute_fixed_income_horizon_return` with
   the curve chained from step 2, the horizon and any rate scenario (`shock_bp`): coupon
   income, reinvestment, pull to par, roll-down and the shock, each named. Its spread to the
   curve is held; say so.
6. **Credit / spread context** (corporates): **sourced, not computed.** The tool does not
   fetch bond prices, compute G-/I-/Z-spread or OAS, or find peer bonds. If a spread or
   rich/cheap conclusion is needed, `search`/`visit` for it and cite it; frame the spread
   conceptually (expected loss + risk premium + liquidity + tax/regulatory/technical) and
   note the decomposition is model-dependent. Do not assert a spread the tools can't ground.
7. **Synthesize.** The rate risk (duration/DV01), the yield read on stated conventions, and
   the credit context you could source, with what's computed vs sourced made clear.

## Principles

- **YTM is a summary, not a forecast**: realised return equals YTM only if held to
  maturity with coupons reinvested at the YTM. For a callable bond quote **yield-to-worst**;
  this analytic tool computes neither YTW nor OAS (valuing the embedded option needs an
  option-adjusted model the harness does not have), so source YTW/OAS or flag the limitation.
- **Compare yields on the same basis**: same compounding, day count, clean/dirty. The tool's
  price is a clean-price approximation without accrued interest.
- **Positive convexity is an asymmetry you pay for.** A callable/MBS gives it up (**negative
  convexity**): use **effective duration** (reprice under a shift), never the analytic
  measure, for anything with optionality.
- **A spread compensates expected loss + risk premium + liquidity** (plus tax/regulatory/
  technical); it is model-dependent and not a pure default probability.
- **Decompose expected return into carry + roll-down + expected yield change** (and, for
  corporates, spread change and expected loss): carry and roll-down are what you earn if
  the curve does not move. **Roll-down reads the curve**: the yield at the bond's maturity
  one horizon shorter, interpolated from the par curve by `compute_fixed_income_yield_curve`,
  a par-curve estimate, not the issuer's own curve; say so.
