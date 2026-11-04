// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * The key hint under a dialog, the same everywhere: `↑↓ move / enter <verb> / esc <what>`.
 * `esc` says what it does here: `close` (a dialog that is done),
 * `back` (a level of a drill-down), `cancel` (a choice that is not made yet).
 */
export type EscapeMeans = "close" | "back" | "cancel"

export function keyHint(enter: string | null, esc: EscapeMeans, move = true): string {
  return [move ? "↑↓ move" : null, enter ? `enter ${enter}` : null, `esc ${esc}`].filter(Boolean).join(" / ")
}
