// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Theme: semantic color tokens for the TUI.
 *
 * A dark theme around the brand teal-green #14C290. The app canvas is
 * terminal-native (no full-screen fill): color comes from accents, semantic tokens
 * and the report palette; only discrete surfaces (menus, inputs) paint a panel.
 * Components use tokens (theme.text, theme.border, …), never raw hex.
 */
import { RGBA } from "@opentui/core"

export { layout } from "./layout"

interface Theme {
  readonly primary: RGBA // brand teal-green: accents, active borders, headings
  readonly accent: RGBA // lighter teal: secondary highlights
  readonly text: RGBA
  readonly textMuted: RGBA
  readonly background: RGBA // dark base (inverted-selection text; canvas is terminal-native)
  readonly backgroundPanel: RGBA // panel fill
  readonly backgroundElement: RGBA // inset elements (input, selected row)
  readonly border: RGBA
  readonly borderActive: RGBA
  readonly reasoningBar: RGBA // the rule beside reasoning: brand green, muted
  readonly success: RGBA
  readonly error: RGBA
  readonly warning: RGBA
  readonly info: RGBA
}

const hex = RGBA.fromHex

export const theme: Theme = {
  primary: hex("#14C290"),
  accent: hex("#3DD9A6"),
  text: hex("#F2F6F4"),
  textMuted: hex("#A3B0AA"),
  background: hex("#0D1311"),
  backgroundPanel: hex("#121A17"),
  backgroundElement: hex("#18221E"),
  border: hex("#34443B"),
  borderActive: hex("#14C290"),
  reasoningBar: hex("#1F5F50"),
  success: hex("#14C290"),
  error: hex("#E5675B"),
  warning: hex("#E0A33E"),
  info: hex("#5AA7E0"),
}

// Report markdown has its own desaturated multi-hue scheme, apart from the app's teal chrome.
export const md = {
  // Heading levels step through our teal-green family: cyan, teal, green.
  h1: hex("#88C0D0"), // frost cyan
  h2: hex("#8FBCBB"), // frost teal
  h3: hex("#A3BE8C"), // aurora green
  bold: hex("#ECEFF4"), // snow (near-white emphasis)
  italic: hex("#D8DEE9"), // light slate
  code: hex("#EBCB8B"), // calm amber
  link: hex("#81A1C1"), // frost blue
  list: hex("#81A1C1"), // frost blue bullets
  citation: hex("#6FB39A"), // muted sage green: inline [N] citations
}

/** Markdown styling for the report. */
export const reportStyles = {
  "markup.heading.1": { fg: md.h1, bold: true },
  "markup.heading.2": { fg: md.h2, bold: true },
  "markup.heading": { fg: md.h3, bold: true },
  "markup.list": { fg: md.list },
  "markup.bold": { fg: md.bold, bold: true },
  "markup.italic": { fg: md.italic, italic: true },
  "markup.raw": { fg: md.code },
  // a link reads as its text, with the URL (the part a terminal opens) in link style
  "markup.link": { fg: theme.textMuted },
  "markup.link.label": { fg: theme.text },
  "markup.link.url": { fg: md.link, underline: true },
  // an inline citation [N]: colored, not underlined (it is a reference, not a link)
  "markup.citation": { fg: md.citation },
  default: { fg: theme.text },
}

/** Shared vertical-scrollbar styling for docked scroll panes (transcript, sources). */
export const scrollbarOptions = {
  showArrows: false,
  trackOptions: { foregroundColor: theme.textMuted, backgroundColor: theme.border },
} as const

/** Shared <select> chrome, spread into the native select in both pickers (DialogSelect,
 *  ListDialog). */
export const selectColors = {
  backgroundColor: theme.backgroundPanel,
  textColor: theme.text,
  descriptionColor: theme.textMuted,
  focusedBackgroundColor: theme.backgroundElement,
  focusedTextColor: theme.text,
  selectedBackgroundColor: theme.primary,
  selectedTextColor: theme.background,
} as const
