// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * CommandPalette: the `/` (and ^K) action menu: a filter input over **grouped**
 * commands. Each group shows a colored header that bolds when the selection is in
 * it; ↑/↓ move between commands only (headers are skipped); enter runs, esc closes.
 *
 * Generic + data-driven: it renders whatever `CommandGroup[]` it's given and
 * derives selection/active-group from a flattened command list. Selection/escape go through useKeyboard (fires while the input holds
 * focus); confirm via the input's onSubmit (avoids a double-trigger on enter).
 */
import { For, Show, createMemo, createSignal } from "solid-js"

import { TextAttributes } from "@opentui/core"
import { useKeyboard } from "@opentui/solid"

import { type Command, type CommandGroup, filterCommandGroups, flattenCommands } from "../lib/commands"
import { keyHint } from "../lib/hints"
import { layout, theme } from "../theme"
import { Field } from "./field"
import { MenuRow } from "./menu-row"
import { Modal } from "./modal"

/** What a row shows on its right: why it cannot run, else its value and key. */
function rowValue(c: Command): string {
  return c.unavailable ?? [c.value, c.shortcut].filter(Boolean).join("  ")
}

export function CommandPalette(props: {
  groups: CommandGroup[]
  onRun: (c: Command) => void
  onCancel: () => void
}) {
  const [query, setQuery] = createSignal("")
  const [index, setIndex] = createSignal(0)
  const groups = createMemo(() => filterCommandGroups(props.groups, query()))
  const flat = createMemo(() => flattenCommands(groups()))
  const active = () => flat()[index()]
  const activeGroupId = () => groups().find((g) => g.commands.some((c) => c.id === active()?.id))?.id

  const [note, setNote] = createSignal("") // why the chosen item did not run
  const run = () => {
    const c = active()
    if (!c) return
    if (c.unavailable) setNote(`${c.title.replace("…", "")}: ${c.unavailable}`)
    else props.onRun(c)
  }

  useKeyboard((e) => {
    if (e.name === "escape") props.onCancel()
    else if (e.name === "up") setIndex((i) => Math.max(0, i - 1))
    // guard the empty-results case: with no matches, keep the index at 0.
    else if (e.name === "down") setIndex((i) => (flat().length ? Math.min(flat().length - 1, i + 1) : 0))
  })

  return (
    <Modal width={layout.width.sm} title="Commands">
      <Field>
        <input
          focused
          placeholder="type to filter…"
          onInput={(v: string) => {
            setQuery(v)
            setIndex(0)
            setNote("")
          }}
          onSubmit={() => run()}
        />
      </Field>
      <box height={1} />
      <Show when={groups().length === 0}>
        <text fg={theme.textMuted}>No matching commands.</text>
      </Show>
      <For each={groups()}>
        {(g) => (
          <box flexDirection="column">
            <text fg={theme.accent} attributes={activeGroupId() === g.id ? TextAttributes.BOLD : undefined}>
              {g.label}
            </text>
            <For each={g.commands}>
              {(c) => {
                return (
                  <MenuRow
                    label={c.title}
                    value={rowValue(c)}
                    selected={active()?.id === c.id}
                    dim={!!c.unavailable}
                  />
                )
              }}
            </For>
          </box>
        )}
      </For>
      <box height={1} />
      <text fg={note() ? theme.warning : theme.textMuted}>{note() || keyHint("run", "close")}</text>
    </Modal>
  )
}
