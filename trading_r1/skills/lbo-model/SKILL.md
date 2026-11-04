---
name: lbo-model
description: "Model a leveraged buyout of a public company: entry at the public multiple plus a stated premium, unlevered cash flows from a three-statement projection, debt paydown over the hold, and the sponsor's IRR and MOIC read through the value-creation bridge."
tags: [lbo, private-equity, irr, moic, modeling]
domain: valuation
requires_tools:
  - data_equity_reference
  - data_equity_fundamentals
  - data_equity_ratios
  - compute_modeling_three_statement
  - compute_valuation_lbo
  - compute_modeling_sensitivity
---

# LBO model

What return a sponsor could earn buying the company with debt, and whether that return comes
from the business or from assuming the market pays more on exit.

## Workflow

1. **Entry.** `data_equity_fundamentals` (statement income: EBITDA; balance: debt, cash) and
   `data_equity_reference` (market cap) give the public EV = market cap + debt − cash and
   EV/EBITDA; `data_equity_ratios` has a provider EV/EBITDA to cross-check (it can be stale;
   the balance-sheet build is the one to use). A sponsor normally pays a control premium, so the entry
   multiple starts at or above the public one; state the premium. Entry below the public
   multiple assumes a dislocation; name why.
2. **Unlevered cash flows.** `compute_modeling_three_statement` for the hold (drivers as in
   `three-statement-model`). Chain `fcff_schedule` (unlevered, so the LBO charges interest
   once, on its own debt) and `ebit_schedule` (caps the interest tax shield) into the LBO;
   exit EBITDA is `prev:<id>.ebitda_schedule[-1]`.
3. **Structure.** Leverage as turns of EBITDA (entry_debt = turns × entry EBITDA), a leveraged
   cost of debt, the tax rate, fees and minimum cash if material, and an exit multiple: equal
   to entry is the defensible base; a higher exit needs a reason.
4. **Run.** `compute_valuation_lbo`. Read IRR and MOIC, the debt schedule, entry and exit
   leverage, and the **value-creation bridge**: EBITDA growth, multiple change, deleveraging,
   fees. A return that rests on multiple expansion is fragile; one from EBITDA growth and
   paydown is within the sponsor's control. Say which it is. A negative cash balance is a
   funding gap; zero proceeds mean the equity was wiped out.
5. **Band it (optional).** `compute_modeling_sensitivity` over entry × exit multiple, or leverage
   (the input is `entry_debt`), on metric `irr` or `moic`.

## Answer shape

IRR and MOIC over the hold; entry and exit EV, debt and equity; the bridge and what it says
about the return; the assumptions, each marked anchored or modeled; the sensitivity band if
run.

## Principles

- The public multiple anchors entry; the premium is stated, not buried.
- Unlevered flows in, interest charged once.
- Read the bridge, not just the IRR.
- No deal verdict: the returns under stated assumptions.
