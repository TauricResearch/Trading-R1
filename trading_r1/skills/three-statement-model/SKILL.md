---
name: three-statement-model
description: "Project a company's income statement, cash flow and balance sheet five years forward from its latest statements and drivers anchored to history and consensus: the free-cash-flow schedules a DCF or LBO needs, the ending capital structure, and a transparent assumption table."
tags: [modeling, projection, three-statement, fcf, dcf, lbo]
domain: modeling
requires_tools:
  - data_equity_fundamentals
  - data_equity_estimates
  - compute_modeling_three_statement
---

# Three-statement model

A driver-anchored forward model. The tool does the arithmetic; the analysis is choosing the
drivers from evidence and saying which are measured and which are assumed.

## Workflow

1. **Historicals.** `data_equity_fundamentals` with statement income, cashflow and balance
   (annual). The tool's drivers are shares of revenue, with opex *above EBITDA*:
   - gross margin = gross_profit / revenue; opex = (gross_profit − ebitda) / revenue (not gross
     minus operating margin, which would count D&A twice);
   - D&A and capex from the cash-flow statement; the effective tax rate;
   - operating working capital = receivables + inventory − payables (the balance sheet's
     `working_capital` line includes cash and short-term debt);
   - opening cash, total debt and net PP&E; the cost of debt as interest expense over average
     debt.

   Derive what isn't reported directly and say so. Year-0 EBITDA should then match the income
   statement's.
2. **Drivers.** `data_equity_estimates` anchors growth (forward EPS against trailing, the next
   report's revenue estimate); consensus for the first years, then a stated fade to a long-run rate. Margins, capex, D&A and working
   capital hold at their recent history unless there is a reason to move them; name it. Debt
   change and payout default to zero unless the question needs them.
3. **Run.** `compute_modeling_three_statement` with the year-0 figures and the driver paths (a
   constant or one value per year).
4. **Read the output.** `fcff_schedule` (unlevered, for a WACC DCF or an LBO) and
   `fcfe_schedule` (to equity, for a cost-of-equity DCF): does the path make sense? The
   balance-sheet residual should be about zero; cash going negative is a funding need, not an
   error; report it.

## Answer shape

The projected statements' key lines by year, the free-cash-flow schedules, the ending debt and
cash, and an assumption table marking each driver measured (history, consensus) or assumed.

## Principles

- Anchor drivers to history and consensus; an assumption without a stated reason is a guess.
- Name the cash flow: FCFF pairs with WACC, FCFE with the cost of equity.
- Chain the schedules into the valuation tools (`prev:<id>.fcff_schedule`) rather than
  retyping them.
