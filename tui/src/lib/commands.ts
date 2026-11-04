// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Command registry: the actions in the `/` (and ^K) palette, as ordered **groups**.
 *
 * Pure + framework-agnostic + data-driven: `buildCommandGroups` maps action callbacks and the
 * current context into `CommandGroup[]`. The palette renders them generically. The structure is
 * stable: every group and item is always listed, and an item that cannot run now says why
 * (`unavailable`).
 *
 * Naming: a verb phrase in sentence case; `…` marks an item that asks for a further choice; a
 * `value` is the current setting an item changes; a `shortcut` is its key.
 */
export interface Command {
  id: string
  title: string
  run: () => void
  value?: string
  shortcut?: string
  unavailable?: string // why it cannot run right now
}

export interface CommandGroup {
  id: string
  label: string
  commands: Command[]
}

export interface CommandActions {
  newSession: () => void
  resumeSession: () => void
  compactContext: () => void
  sessionStatus: () => void
  chooseModel: () => void
  chooseMode: () => void
  copyAnswer: () => void
  exportReport: () => void
  sendReport: () => void
  sendChannels: () => void
  scheduledReports: () => void
  browseTools: () => void
  help: () => void
  apiKeys: () => void
  backgroundService: () => void
  settings: () => void
  quit: () => void
}

export interface CommandContext {
  mode: string
  model: string
  running: boolean // a turn is running
  hasAnswer: boolean // a finished answer to copy or export
  hasSession: boolean // a session with turns, to compact
}

export function buildCommandGroups(actions: CommandActions, ctx: CommandContext): CommandGroup[] {
  const busy = ctx.running ? "wait for this turn" : undefined
  const noAnswer = ctx.hasAnswer ? undefined : "no answer yet"
  return [
    {
      id: "session",
      label: "Session",
      commands: [
        { id: "new", title: "New session", run: actions.newSession, shortcut: "^N", unavailable: busy },
        { id: "resume", title: "Resume session…", run: actions.resumeSession, unavailable: busy },
        {
          id: "compact",
          title: "Compact context…",
          run: actions.compactContext,
          unavailable: busy ?? (ctx.hasSession ? undefined : "no session yet"),
        },
        { id: "status", title: "Session status", run: actions.sessionStatus },
      ],
    },
    {
      id: "research",
      label: "Research",
      commands: [
        { id: "model", title: "Model…", run: actions.chooseModel, value: ctx.model },
        {
          id: "mode",
          title: "Mode…",
          run: actions.chooseMode,
          value: ctx.mode,
          shortcut: "shift+tab",
        },
      ],
    },
    {
      id: "output",
      label: "Output",
      commands: [
        { id: "copy", title: "Copy answer", run: actions.copyAnswer, unavailable: noAnswer },
        { id: "export", title: "Export report…", run: actions.exportReport, unavailable: noAnswer },
        { id: "send", title: "Send report…", run: actions.sendReport, unavailable: noAnswer },
        { id: "channels", title: "Send channels…", run: actions.sendChannels },
        { id: "jobs", title: "Scheduled reports…", run: actions.scheduledReports },
      ],
    },
    {
      id: "reference",
      label: "Reference",
      commands: [
        { id: "browse", title: "Tools & skills…", run: actions.browseTools },
        { id: "help", title: "Help", run: actions.help },
      ],
    },
    {
      id: "app",
      label: "App",
      commands: [
        { id: "keys", title: "API keys…", run: actions.apiKeys },
        { id: "service", title: "Background service…", run: actions.backgroundService },
        { id: "settings", title: "Settings…", run: actions.settings },
        { id: "quit", title: "Quit", run: actions.quit, shortcut: "^C" },
      ],
    },
  ]
}

/** The selectable commands in display order (headers are not selectable). */
export function flattenCommands(groups: CommandGroup[]): Command[] {
  return groups.flatMap((g) => g.commands)
}

/** Narrow each group's commands by a case-insensitive substring of the title or the value;
 *  drop the groups left empty. */
export function filterCommandGroups(groups: CommandGroup[], query: string): CommandGroup[] {
  const q = query.trim().toLowerCase()
  if (!q) return groups
  const hit = (c: Command) => c.title.toLowerCase().includes(q) || (c.value ?? "").toLowerCase().includes(q)
  return groups.map((g) => ({ ...g, commands: g.commands.filter(hit) })).filter((g) => g.commands.length > 0)
}
