---
name: options-strategy
description: "Analyze an option or multi-leg option strategy: price each leg and its Greeks with Black-Scholes, aggregate the position, map the payoff and breakevens, and frame the implied-vs-realized volatility view, on the right conventions."
tags: [derivatives, options, greeks, black-scholes, volatility]
domain: derivatives
requires_tools:
  - data_market_prices
  - data_equity_ratios
  - data_equity_reference
  - data_market_rates
  - compute_derivatives_implied_vol
  - compute_derivatives_black_scholes
  - compute_derivatives_binomial
  - compute_derivatives_position_greeks
  - compute_risk_volatility
---

# Options strategy analysis

Price the legs, read the Greeks, map the payoff, and be explicit that an options position
is a view on **volatility**, not just direction.

## Workflow

1. **Define the strategy and its legs.** State each leg: long/short, call/put, strike,
   expiry, quantity. Name the structure (covered call, protective put, vertical spread,
   straddle/strangle, collar…) and the view it expresses.
2. **Set the inputs.** The data tools carry no option quotes: the legs' strikes, expiry and
   prices come from the user, spot from `data_equity_reference` and `years` to expiry from the
   dates. `data_market_rates` for the rate at the tenor nearest the expiry (its
   yields are bond-equivalent, semiannual, the bills included), converted to a
   **continuously-compounded** rate, r = 2*ln(1 + y/2); `data_equity_ratios` for the dividend
   yield. **Implied vol:** solve it from each leg's mid with `compute_derivatives_implied_vol`
   at those `years`. Without a two-sided quote there is no market IV: a stated volatility
   assumption may be used only for scenario pricing and must never be described as
   market-implied.
3. **Gauge realized volatility.** `compute_risk_volatility` on the `data_market_prices`
   closes (realized and EWMA, annualized on the series' own calendar). This is the realized
   benchmark the implied vol is compared against.
4. **Price each leg.** `compute_derivatives_black_scholes` per leg -> price and Greeks
   (Δ, Γ, vega/pt, θ/day, ρ). Listed US equity options are American: for puts, and calls on
   dividend payers, `compute_derivatives_binomial` (exercise american) gives the early-exercise
   value.
5. **Aggregate the position.** `compute_derivatives_position_greeks` nets the legs: premium
   (debit or credit) and net Greeks in currency terms: dollar delta, dollar gamma (state the convention: change in dollar delta per 1%
   move), vega per point, theta per day.
6. **Payoff and breakevens.** Map the payoff at expiry across the underlying range; state
   max gain, max loss, and the breakeven(s).
7. **The volatility view.** Compare the sourced/supplied implied vol against the realized
   estimate (if you priced on an assumed vol, frame the result as a scenario, not a market
   read). A delta-hedged option is, **before hedging costs**, a bet on realized vs implied vol
   (discrete hedging, jumps and a moving implied vol add slippage); an outright adds
   direction on top.

## Principles

- **Long options = long gamma, long vega, short theta**; short options the reverse. Know
  which triple you hold before anything else.
- **Delta-neutral is not riskless**: gamma and vega remain. Report Greeks in currency, not
  raw units (a gamma of 0.015 means nothing until it is dollar-gamma).
- **N(d₂) is the risk-neutral probability a *call* finishes ITM; for a put it is N(−d₂).**
  Either way it is a pricing weight, not a real-world probability; never report it as the
  odds of profit.
- **Volatility is the input the market does not hand you and, for most equity options, the
  one the value is most sensitive to.** In the market, BSM's real output is implied vol
  (traders invert it on the market price): `compute_derivatives_implied_vol` does that from a
  quoted mid. Use forward-looking implied, not trailing realized, as the pricing input.
- **BSM is European.** For American options with early-exercise value (dividend-paying
  calls, most puts) use the binomial lattice; do not quote the BSM price as the American
  value.
