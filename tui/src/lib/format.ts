// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** How numbers and times are written on screen. Pure. */

/** A context-meter token count in thousands: `131072` -> `131k`. */
export function tokensK(n: number): string {
  return `${Math.round(n / 1000)}k`
}

/** Tokens in a few characters: 950, 12k, 1.5M. */
export function tokens(n: number): string {
  if (n >= 1_000_000) return `${+(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${Math.round(n / 1000)}k`
  return String(n)
}

/** An ISO timestamp to the minute, in UTC: `2026-10-23 08:00 UTC`; empty when there is none. */
export function utcMinute(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 16).replace("T", " ") + " UTC" : ""
}

/** The date part of an ISO timestamp: `2026-10-23`. */
export function day(iso: string): string {
  return iso.slice(0, 10)
}
