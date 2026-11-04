// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Logo: the Trading-R1 mark and wordmark.
 *
 * Welcome screen only. Static, with no subtitle.
 * The mark is the brand's 8x8 dot matrix (filled dots in the brand green, the rest hollow); the
 * wordmark art lives in a sibling `logo.txt` that ships inside the TUI bundle, read relative to
 * this module so it resolves in both a source checkout and an installed wheel; each row renders
 * as its own <text>.
 */
import { readFileSync } from "node:fs"
import { join } from "node:path"

import { For } from "solid-js"

import { theme } from "../theme"

/** The brand mark, one string per row: `#` a filled dot, `.` a hollow one. */
export const MARK = [
  "#......#",
  "#......#",
  "########",
  ".##..##.",
  "..#..#..",
  "..#..#..",
  "..####..",
  "...##...",
]

function loadWordmark(): string[] {
  try {
    const raw = readFileSync(join(import.meta.dir, "logo.txt"), "utf8")
    return raw.replace(/\n+$/, "").split("\n")
  } catch {
    return ["Trading-R1"]
  }
}

const WORDMARK = loadWordmark()

function Mark() {
  return (
    <box flexDirection="column" flexShrink={0}>
      <For each={MARK}>
        {(row) => (
          <text>
            <For each={[...row]}>
              {(dot) => (
                <span style={{ fg: dot === "#" ? theme.primary : theme.textMuted }}>
                  {dot === "#" ? "●" : "○"}{" "}
                </span>
              )}
            </For>
          </text>
        )}
      </For>
    </box>
  )
}

export function Logo() {
  return (
    <box flexDirection="row" alignItems="center" gap={3} flexShrink={0}>
      <Mark />
      <box flexDirection="column" flexShrink={0}>
        <For each={WORDMARK}>{(line) => <text fg={theme.primary}>{line}</text>}</For>
      </box>
    </box>
  )
}
