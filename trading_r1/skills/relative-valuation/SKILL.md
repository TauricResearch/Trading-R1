---
name: relative-valuation
description: "Value an equity against its peers: a peer set chosen on industry, size and business model, peer-median trading multiples applied to the company's metrics for an implied range, and where the multiples agree or diverge, cross-read against its own valuation ratios."
tags: [equity, valuation, comps, relative-valuation, multiples]
domain: valuation
requires_tools:
  - data_equity_reference
  - data_equity_ratios
  - data_equity_comps
  - data_equity_fundamentals
  - compute_valuation_comps
  - data_market_symbol_search
---

# Relative valuation

What the market pays for similar businesses today, and where this one sits against them. It
complements an intrinsic (DCF) view; a gap between the two is itself informative.

## Workflow

1. **Classify the company.** `data_equity_reference` for sector, industry, size and the business
   it is in.
2. **Choose the peers** on three axes: the same industry (multiples are sector-conditional:
   banks trade on book, software on sales, utilities on EBITDA), a similar size (within an order
   of magnitude), and a similar business model (revenue mix, capital intensity). A user's peer
   list is used as given. To find candidates, `search` for the industry's listed companies and
   `data_market_symbol_search` to check each ticker, then `data_equity_reference` for size. Name the peers and why each fits; if no clean set exists, say so; that
   is a finding.
3. **The company's own multiples.** `data_equity_ratios` for P/E (trailing and forward),
   EV/EBITDA, EV/revenue, P/S and PEG. P/B is in `data_equity_fundamentals` (summary,
   `price_to_book`); for book-priced sectors take the peer median of it with `calc`; the comps
   tools don't cover P/B.
4. **Comps.** `data_equity_comps` with the peers: each peer's multiples, growth and margins, and
   peer medians (medians, since multiples have heavy right tails) applied to the company's
   metrics for an implied value per share by multiple and a range against the price. Read them
   from the tool rather than re-deriving. For peers whose multiples you have from elsewhere,
   `compute_valuation_comps` runs the same arithmetic. A peer whose P/E is meaningless (losses)
   stays in the table as n/a rather than dropping out.
5. **Read it.** Foreground the multiples that matter for the sector. Where they agree, the
   relative read is strong; where they diverge (a growth premium visible on P/S but not on P/E, a
   capital structure that moves EV multiples), explain why; don't average it away. Tie any premium or discount to growth, margins or returns.

## Answer shape

The company and its classification; the peer set and why; a multiples table (the company and
each peer, the medians); the implied values by multiple and the range against the price; where
the multiples agree and diverge. State at the figures they affect: a small or mixed peer set,
missing metrics, a multiple that doesn't apply (P/E on losses). Mark which figures came from the tools and which
you derived.

## Principles

- Peer choice drives the answer: a bad peer set makes a precise-looking range meaningless.
- Multiples are sector-conditional; lead with the ones the sector is priced on.
- A premium or discount is a finding only once tied to fundamentals.
- Relative value states where the stock sits against peers; pair it with a DCF for a view
  on intrinsic value.
