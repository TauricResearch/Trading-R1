// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Execution modes (client mirror of the backend modes): the loadout the user
 * cycles with shift+tab and that threads into `/research`.
 *
 *   auto        full toolkit; the agent decides (default)
 *   research    web-first deep research; leans toward asking to clarify an ambiguous ask
 *   analytical  numbers-first: data + valuation tools, web for context
 */
export const MODES = ["auto", "research", "analytical"] as const
export type Mode = (typeof MODES)[number]

export const DEFAULT_MODE: Mode = "auto"

/** Coerce an untrusted string (e.g. the `TR1_MODE` launch env) to a valid mode, or the
 *  default. Lets `trading-r1 --mode research` seed the TUI's initial mode. */
export function parseMode(s: string | null | undefined): Mode {
  return (MODES as readonly string[]).includes(s ?? "") ? (s as Mode) : DEFAULT_MODE
}

const MODE_HELP: Record<Mode, string> = {
  auto: "Full toolkit; the agent decides",
  research: "Web first: deep research that asks when the question is open",
  analytical: "Numbers first: data and valuation tools, the web for context",
}

/** The Mode picker's rows: the current one marked with a dot. */
export function modeOptions(current: Mode): { name: string; description: string; value: string }[] {
  return MODES.map((m) => ({
    name: `${m === current ? "●" : " "} ${m}`,
    description: `  ${MODE_HELP[m]}`,
    value: m,
  }))
}

/** Next mode in the cycle (wraps). */
export function nextMode(mode: Mode): Mode {
  return MODES[(MODES.indexOf(mode) + 1) % MODES.length]
}
