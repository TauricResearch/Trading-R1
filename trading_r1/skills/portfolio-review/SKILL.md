---
name: portfolio-review
description: "Review a return series' performance and risk: annualized return/vol, the risk-adjusted ratios with their statistical significance, downside/VaR, and the benchmark-relative read (beta, information ratio, capture), judged on the right measure for the mandate."
tags: [portfolio, performance, risk, sharpe, information-ratio, var]
domain: portfolio
requires_tools:
  - compute_statistics_descriptive
  - compute_portfolio_performance
  - compute_risk_var
  - compute_statistics_regression
---

# Portfolio review

Return and risk together, on the measure that fits the mandate, and always ask whether the
track record is long enough to mean anything.

## Workflow

1. **Frame the mandate.** Absolute or benchmark-relative? That decides the headline measure:
   Sharpe for a standalone book, the **information ratio** for a benchmarked one. Note the
   return frequency (monthly/daily) and the risk-free rate.
2. **Distribution first.** `compute_statistics_descriptive` on the return series: mean,
   volatility, and especially **skewness and excess kurtosis**: a good Sharpe on a
   fat-tailed, negatively-skewed series is hiding tail risk.
3. **Risk-adjusted performance.** `compute_portfolio_performance` (pass the benchmark if
   there is one) -> annualized return/vol, Sharpe, Sortino, Calmar, max drawdown, and, with a
   benchmark, beta, Jensen's alpha, tracking error, information ratio, Treynor, and up/down
   capture. Read the significance the tool returns: the **Sharpe t-stat** for an absolute
   mandate, the **information-ratio t-stat** for a benchmarked one.
4. **Downside.** `compute_risk_var` for the tail loss; note this tool takes a **price
   series**, so build a cumulative wealth path from the returns first (start at 100 and
   compound). A normal (parametric) VaR understates the tail when kurtosis is high (step 2),
   and √-scaling a historical VaR to a longer horizon is only a rough approximation.
5. **Systematic exposure.** `compute_statistics_regression` of the portfolio's excess
   returns on the benchmark's -> beta and its confidence interval. The intercept is the
   **per-period** estimated alpha; annualize it (× periods) to compare; report it as
   estimated alpha, but do not claim outperformance or skill unless its t is significant.
6. **Synthesize.** Judge on the mandate's measure, state the significance, and flag the
   tail/skew and the benchmark-relative read.

## Principles

- **The information ratio, not the Sharpe, is the right measure for a benchmarked mandate**:
  Sharpe rewards taking benchmark risk.
- **Statistical significance of a track record: t = Sharpe × √years** (the ordinary
  mean-excess-return t-stat, assuming i.i.d. returns). A Sharpe of 1.0 needs ~4 years to
  reach t = 2; most records are too short. Report ratios with their t-stat, never bare.
- **Capital weights are not risk weights.** From a single return series you can only report
  total risk; where you have the holdings and a covariance matrix, report component risk
  contributions.
- **A single ratio hides the tails.** Pair Sharpe with Sortino/Calmar, the drawdown, and
  skew/kurtosis; the √m annualization assumes i.i.d. returns and overstates the Sharpe of
  autocorrelated (credit, illiquid) series.
- **Correlation is unstable and rises in crises**: diversification measured in calm periods
  overstates the benefit exactly when it is needed.
