// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Sending a report: what the outcome of a send says, by channel name and in plain words. */
import type { Outcome } from "./api"

export interface SendSummary {
  sent: string[] // went out now
  already: string[] // went out earlier: nothing was sent now
  failed: Outcome[] // did not go: can be tried again
  unsure: Outcome[] // may have gone: sent again only if asked
}

export function summarize(outcomes: Outcome[]): SendSummary {
  const fresh = outcomes.filter((o) => !o.repeated)
  return {
    sent: fresh.filter((o) => o.status === "sent").map((o) => o.channel),
    already: outcomes.filter((o) => o.repeated && o.status === "sent").map((o) => o.channel),
    failed: outcomes.filter((o) => o.status === "failed"),
    unsure: outcomes.filter((o) => o.status === "unknown"),
  }
}

/** A channel's failure in plain terms where the cause is a known one, else as the channel said
 *  it: an address or a path in it is what a person needs in order to fix it. Keys never reach
 *  here: the channel removes them. */
export function plainReason(o: Pick<Outcome, "detail">): string {
  const why = (o.detail ?? "").replace(/\s*\(0 of \d+ parts delivered\)$/, "").trim()
  if (/\b401\b/.test(why)) return "its token was refused"
  if (/\b403\b/.test(why)) return "it may not write there; check that the bot is started"
  if (/\b429\b/.test(why)) return "too many messages; try again shortly"
  return why
}

/** A failed channel as one line a person can act on. */
export function failureLine(o: Outcome): string {
  const why = plainReason(o)
  return why ? `${o.channel}: ${why}` : `${o.channel}: not sent`
}
