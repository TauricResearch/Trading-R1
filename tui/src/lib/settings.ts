// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * User preferences: what the Settings dialog lists and how a selection changes one. Pure: the
 * app owns the state and persists a change through `PUT /settings`.
 */
import { type Mode, nextMode, parseMode } from "./mode"

const TOOL_DETAILS = ["brief", "full"] as const
export type ToolDetails = (typeof TOOL_DETAILS)[number]

export interface Prefs {
  verify: boolean
  grounding: boolean
  auto_compact: boolean
  show_reasoning: boolean
  default_mode: Mode
  tool_details: ToolDetails
  side_panel: boolean
  sync: boolean
  report_markdown: boolean
  report_html: boolean
}

export const DEFAULT_PREFS: Prefs = {
  verify: false,
  grounding: false,
  auto_compact: true,
  show_reasoning: true,
  default_mode: "auto",
  tool_details: "brief",
  side_panel: true,
  sync: true,
  report_markdown: true,
  report_html: false,
}

export type PrefKey = keyof Prefs

/** What the server reports for the preferences (it sends the values in effect). */
export function prefsFrom(raw: Partial<Record<PrefKey, unknown>> | undefined): Prefs {
  return {
    verify: raw?.verify === true,
    grounding: raw?.grounding === true,
    auto_compact: raw?.auto_compact !== false,
    show_reasoning: raw?.show_reasoning !== false,
    default_mode: parseMode(typeof raw?.default_mode === "string" ? raw.default_mode : undefined),
    tool_details: raw?.tool_details === "full" ? "full" : "brief",
    side_panel: raw?.side_panel !== false,
    sync: raw?.sync !== false,
    report_markdown: raw?.report_markdown !== false,
    report_html: raw?.report_html === true,
  }
}

/** How a row changes when selected: a switch flips, a choice steps to the next value, an
 *  action opens a further dialog. */
type RowKind = "switch" | "choice" | "action"

interface SettingRow {
  key: PrefKey | "notes" | "sync_more"
  group: string
  label: string
  help: string
  kind: RowKind
}

export const SETTING_ROWS: SettingRow[] = [
  {
    key: "verify",
    group: "Research",
    label: "Verification",
    help: "Recheck the figures in a report with tools before answering. Takes longer.",
    kind: "switch",
  },
  {
    key: "grounding",
    group: "Research",
    label: "Source review",
    help: "Reread a report against its sources and soften what they do not support. Takes longer. Ignored when Verification is on.",
    kind: "switch",
  },
  {
    key: "default_mode",
    group: "Research",
    label: "Default mode",
    help: "The mode the app opens in. Shift+Tab changes the mode of the running session.",
    kind: "choice",
  },
  {
    key: "auto_compact",
    group: "Research",
    label: "Auto-compact",
    help: "Compact the context before it fills the model's window.",
    kind: "switch",
  },
  {
    key: "show_reasoning",
    group: "Display",
    label: "Show reasoning",
    help: "Show the model's thinking in the transcript.",
    kind: "switch",
  },
  {
    key: "tool_details",
    group: "Display",
    label: "Tool details",
    help: "How much of each tool call to show: brief, or every argument by name.",
    kind: "choice",
  },
  {
    key: "side_panel",
    group: "Display",
    label: "Side panel",
    help: "The plan, data records and sources beside the transcript.",
    kind: "switch",
  },
  {
    key: "report_markdown",
    group: "Reports",
    label: "Also save as Markdown",
    help: "Also save Markdown beside each PDF. Chat and email still get the PDF.",
    kind: "switch",
  },
  {
    key: "report_html",
    group: "Reports",
    label: "Also save as HTML page",
    help: "Also save each report as a single HTML page.",
    kind: "switch",
  },
  {
    key: "sync",
    group: "Sync",
    label: "Session sync",
    help: "Keep a copy of your sessions in your Tauric account: the questions, model requests and responses, tool calls and results. Scheduled and Telegram sessions are never copied.",
    kind: "switch",
  },
  {
    key: "sync_more",
    group: "Sync",
    label: "Copy or restore sessions…",
    help: "Copy the sessions already on this machine, or get back one that only your account has.",
    kind: "action",
  },
  {
    key: "notes",
    group: "Memory",
    label: "Remembered notes…",
    help: "What the agent has noted about you in chat sessions. Forget any of it.",
    kind: "action",
  },
]

const onOff = (v: boolean) => (v ? "on" : "off")

/** The value as the dialog shows it. */
export function display(key: PrefKey, prefs: Prefs): string {
  const v = prefs[key]
  return typeof v === "boolean" ? onOff(v) : String(v)
}

/** The key hint for a row: what enter does there. */
export function enterVerb(kind: RowKind): string {
  return kind === "switch" ? "toggle" : kind === "choice" ? "change" : "open"
}

/** The change a selection makes: a switch flips, the default mode steps to the next one. */
export function nextValue(key: PrefKey, prefs: Prefs): Partial<Prefs> {
  if (key === "default_mode") {
    return { default_mode: nextMode(prefs.default_mode) }
  }
  if (key === "tool_details") {
    return { tool_details: prefs.tool_details === "brief" ? "full" : "brief" }
  }
  return { [key]: !prefs[key] } as Partial<Prefs>
}
