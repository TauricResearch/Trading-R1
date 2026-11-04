---
name: industry-analysis
description: "Analyze an industry's structure and profitability: the value chain, Porter's five forces as a hypothesis tested against returns, the lifecycle stage, concentration and competitive dynamics, and the moats of the leaders, based on web research and a peer cross-check."
tags: [equity, industry, five-forces, moats, competitive-strategy]
domain: equity
requires_tools:
  - data_equity_comps
---

# Industry analysis

Structure shapes long-run profitability. Map the value chain, run the five forces as a
**hypothesis to test against actual returns**, place the industry in its lifecycle, and
identify who has a durable advantage.

## Workflow

1. **Define the industry and value chain.** `search` + `visit` to scope the boundary using
   **substitution**: group products/firms that are demand-side substitutes (buyers switch
   between them) and supply-side substitutes (the same assets can make them); don't just take
   a GICS/NAICS label. Map the steps from input to end customer and where profit pools sit.
2. **Porter's five forces, as a hypothesis.** Ground each in evidence you read: rivalry,
   buyer power, supplier power, threat of new entrants (barriers, capital intensity, scale
   economies), and substitutes. The forces generate a **hypothesis about the average
   sustainable profitability**; test it against the industry's actual returns on capital
   rather than treating it as a verdict. **High concentration does not by itself mean weak
   rivalry**: a concentrated industry with undifferentiated products and excess capacity
   still competes profits away.
3. **Lifecycle stage.** Embryonic -> growth -> **shakeout** -> mature -> decline. The shakeout
   (slowing growth, consolidation, exits, peak-then-falling margins) is distinct from both
   growth and maturity and often the most important call. The stage sets the expected growth,
   margin trajectory and where returns on capital are heading. (This is the **industry**
   lifecycle, not the macro/business cycle.)
4. **Concentration and dynamics.** Who is gaining or losing share, and how concentrated is
   the industry? `data_equity_comps` on the named players cross-checks size, margins, growth
   and returns against the qualitative picture.
5. **Moats of the leaders.** Which participants have a **durable** advantage: network
   effects, switching costs, intangibles/brand, or a genuine structural unit-cost advantage?
   **Being large is not a moat** unless scale produces a demonstrable cost or demand
   advantage. The quantitative signature of a moat is **persistent returns on capital above
   the cost of capital across a full cycle** (including incremental ROIC); note that comps
   returns ROE, not ROIC/WACC, so the ROIC test needs figures sourced from research.
6. **The structural read.** How attractive is the industry, where is it in its lifecycle, and
   which players are structurally advantaged, with each claim sourced to what you read, and
   the **direction of change** in each force, not just its current level.

## Principles

- **Industry structure sets the average; competitive position sets the spread around it.**
  Separate the two: a great company in a poor industry and a mediocre one in a great
  industry are both common.
- **Five forces is a static, cross-sectional hypothesis**: the valuable analysis is the
  **direction of change** in each force, tested against actual industry ROIC, not asserted.
- **A moat is durable advantage measured by persistent ROIC above WACC**, not a good product
  and not mere size; most apparent advantages are competed away.
- **The lifecycle stage reframes the metrics**: rapid growth with negative-to-breakeven cash
  flow is normal in the growth stage but must be explained by investment economics, not
  normalized automatically; the same profile late is a warning.
- **Concentration matters, but do not assert regulatory thresholds** (HHI cutoffs, market
  definitions); describe the dynamics and cite the source; the legal lines move.
