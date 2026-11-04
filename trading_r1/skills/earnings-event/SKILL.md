---
name: earnings-event
description: "Brief a stock into its next earnings report: the move the options market prices in (from an at-the-money straddle the user supplies), against the consensus, the reported record and how the stock has actually moved on past reports."
tags: [options, earnings, event, implied-move, straddle]
domain: equity
requires_tools:
  - data_equity_earnings
  - data_market_prices
  - data_equity_estimates
  - data_equity_filings
  - data_equity_reference
---

# Earnings event

What move the options market prices into a report, and how that compares with what the stock
usually does. The data tools carry no option quotes: the straddle comes from the user (the
at-the-money call and put prices at the expiry that follows the report), and without it the
brief rests on the realized record. For positions or strategies around the event, use
`options-strategy`.

## Workflow

1. **The event.** `data_equity_earnings` for the next report's date, the consensus EPS and
   revenue estimates, and the reported EPS and revenue of recent quarters.
2. **The bracketing expiry.** Ask the user for the straddle if they have not given it, and for
   the **first expiry whose close falls after the report**: a far-dated expiry carries months
   of non-event volatility and overstates the event move. Say which expiry is used.
3. **The straddle.** The at-the-money call and put prices the user gives; spot from
   `data_equity_reference`. A one-sided or last-trade quote is said to be one.
4. **The move** (use `calc`; S = spot, C and P the prices, T = years to the expiry):
   - straddle = C + P, the expected absolute move to expiry, in currency;
   - implied move = straddle / S; priced-in range = S ± straddle;
   - the implied volatility the straddle embeds ≈ straddle / (S*√(2T/π)); the one-standard-
     deviation move is straddle*√(π/2): the straddle is the expected *absolute* move, √(2/π)
     of one standard deviation, so keep the two apart.
5. **Context.** `data_market_prices` for recent realized volatility, and the reaction on past
   report dates if you pull daily bars around them; `data_equity_estimates` for the consensus
   going into the print (price targets, rating spread, forward EPS); `data_equity_filings`
   (form 8-K) for the last results release to `visit`.

## Answer shape

The dated event and the expiry read from; the implied move (± % and currency) and range; the
implied volatility; the comparison with realized moves and the reported record. State at the figures they affect: an
expiry past the event, stale quotes.

## Principles

- The right expiry first: the commonest error is reading the move off a far-dated straddle.
- The move comes from market prices (the straddle), not a model.
- The expected move is not the one-standard-deviation move; label which you give.
- Numbers and comparisons only: no view on direction or on whether the move is cheap or rich.
