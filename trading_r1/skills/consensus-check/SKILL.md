---
name: consensus-check
description: "Summarize the sell-side view on an equity: analyst price targets, forward EPS/revenue estimates, the rating spread, and the reported earnings record, and corroborate it against recent guidance from the web."
tags: [equity, estimates, consensus, analyst]
domain: equity
requires_tools:
  - data_equity_estimates
  - data_equity_earnings
---

# Consensus check

What the Street expects for a name, and whether recent news supports it.

## Workflow

1. **Consensus snapshot.** `data_equity_estimates` for price targets (mean/median, range vs
   current), trailing and forward EPS, the next report's EPS and revenue estimate, analyst
   counts, and the strong-buy to strong-sell spread.
2. **Track record.** `data_equity_earnings` for the reported EPS and revenue of recent
   quarters (growth on the year before) and the next report's date. Past consensus estimates,
   surprises and estimate revisions are not available from the tools.
3. **Direction.** `search` + `visit` for recent, authoritative coverage of estimate changes,
   rating changes by firm and the latest guidance. Ground each specific claim in a page you
   read, and say that the revision trend comes from the web, not the tools.
4. **Report.** The consensus expectation and the dispersion (tight vs wide targets), the
   rating skew, the reported trend, and what the coverage says about which way estimates are
   moving.

## Principles

- These are *estimates*: present them as consensus expectations, not facts, and note
  the dispersion (a wide target range is itself the signal).
- Pair the tool's consensus numbers with what changed recently; a stale consensus and
  a fresh guidance cut tell different stories.
- Attribute web-sourced specifics to the page you read; mark anything you couldn't
  confirm as unverified.
