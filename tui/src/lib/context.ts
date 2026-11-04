// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * The context meter in the rail: how full the model's window is, and where the context compacts.
 * Pure: the rail renders what this returns.
 */
import type { StatusInfo, Usage } from "./api"
import { tokensK } from "./format"

export interface ContextMeter {
  usage: string // "Context 64%, 168k / 262k"
  compaction: string // where it compacts, or that it does not
  warn: boolean // past the compaction point (or, with it off, 90% of the window)
}

const DEFAULT_WINDOW = 262144 // shown before the first status so the meter never pops in

export function contextMeter(status: StatusInfo | null): ContextMeter {
  const used = status?.tokens_used ?? 0
  const window = status?.context_window ?? DEFAULT_WINDOW
  const pct = status?.used_pct ?? 0
  const at = status?.compact_at ?? null
  return {
    usage: `Context ${pct}%, ${tokensK(used)} / ${tokensK(window)}`,
    compaction: at === null ? "Auto-compact off" : `Compacts at ${tokensK(at)}`,
    warn: at === null ? pct >= 90 : used >= at,
  }
}

/** Fold a live `usage` event into the rail's StatusInfo, keeping the model + turn count
 *  from the last /status (the event only carries the running token estimate). */
export function mergeUsage(prior: StatusInfo | null, ev: Usage, model: string): StatusInfo {
  return {
    model: model || prior?.model || "",
    context_window: ev.context_window,
    tokens_used: ev.tokens_used,
    used_pct: ev.used_pct,
    compact_at: ev.compact_at,
    turns: prior?.turns ?? 0,
  }
}
