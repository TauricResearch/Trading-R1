// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Session sync: how a session's state and the whole's state are written on screen. Pure. */

export type SyncMark = "synced" | "pending" | "full" | "error" | "off"

export interface SyncStatus {
  enabled: boolean
  state: string
  counts: Record<SyncMark, number>
  deleting: number
  error: string
  usage: { used_bytes: number; allowance_bytes: number } | null
}

export interface RemoteSession {
  id: string
  label: string | null
  size: number
  updated_at: string | null
}

/** One character per state, kept to a column so titles line up: ✓ copied, … waiting, ! the
 *  archive is full, ✗ stopped, blank for a session that is not copied. */
export const SYNC_GLYPH: Record<SyncMark, string> = {
  synced: "✓",
  pending: "…",
  full: "!",
  error: "✗",
  off: " ",
}

export function syncGlyph(mark: string | undefined): string {
  return SYNC_GLYPH[mark as SyncMark] ?? " "
}

/** A size in bytes in a few characters: 950 B, 12 KB, 1.5 MB. */
export function bytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ["KB", "MB", "GB", "TB"]
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v >= 10 ? Math.round(v) : +v.toFixed(1)} ${units[i]}`
}

/** What the Settings row says under its help: the state, the counts, the archive's use, the last
 *  problem. One short line each. */
export function syncLines(s: SyncStatus): string[] {
  const out = [`State: ${s.state}`]
  const parts = (["synced", "pending", "full", "error"] as const)
    .filter((k) => s.counts[k] > 0)
    .map((k) => `${s.counts[k]} ${k}`)
  if (parts.length) out.push(`Sessions: ${parts.join(", ")}`)
  if (s.usage) out.push(`Account: ${bytes(s.usage.used_bytes)} of ${bytes(s.usage.allowance_bytes)}`)
  if (s.error) out.push(`Last problem: ${s.error}`)
  return out
}

/** The picker row of a session the archive holds that is not here. */
export function remoteOption(s: RemoteSession): { name: string; description: string; value: string } {
  return {
    name: s.label || s.id,
    description: `${bytes(s.size)}, ${(s.updated_at ?? "").slice(0, 10)}`,
    value: s.id,
  }
}
