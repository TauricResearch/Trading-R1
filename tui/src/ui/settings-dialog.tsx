// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * SettingsDialog: the preferences as aligned rows, label left and value right. A change is
 * saved as it is made, so esc closes (nothing to cancel); the help line under the list explains
 * the row under the cursor and the key hint says what enter does there.
 */
import { For, Show, createSignal } from "solid-js"

import { useKeyboard } from "@opentui/solid"

import { keyHint } from "../lib/hints"
import { type PrefKey, type Prefs, SETTING_ROWS, display, enterVerb } from "../lib/settings"
import { layout, theme } from "../theme"
import { MenuRow } from "./menu-row"
import { Modal } from "./modal"

export function SettingsDialog(props: {
  prefs: () => Prefs
  onSelect: (key: PrefKey | "notes" | "sync_more") => void
  extra?: (key: PrefKey | "notes" | "sync_more") => string[] // live lines under a row's help
  onClose: () => void
  selected?: number // the row the cursor starts on
}) {
  const [index, setIndex] = createSignal(props.selected ?? 0)
  const row = () => SETTING_ROWS[index()]
  useKeyboard((e) => {
    if (e.name === "escape") props.onClose()
    else if (e.name === "up") setIndex((i) => (i + SETTING_ROWS.length - 1) % SETTING_ROWS.length)
    else if (e.name === "down") setIndex((i) => (i + 1) % SETTING_ROWS.length)
    else if (e.name === "return" || e.name === "enter") props.onSelect(row().key)
  })
  return (
    <Modal width={layout.width.md} title="Settings">
      <For each={SETTING_ROWS}>
        {(r, i) => (
          <>
            <Show when={i() === 0 || SETTING_ROWS[i() - 1].group !== r.group}>
              <text fg={theme.accent}>{r.group}</text>
            </Show>
            <MenuRow
              label={r.label}
              value={r.kind === "action" ? "" : display(r.key as PrefKey, props.prefs())}
              selected={index() === i()}
            />
          </>
        )}
      </For>
      <box height={1} />
      <text fg={theme.textMuted} wrapMode="word">
        {[row().help, ...(props.extra?.(row().key) ?? [])].join("\n")}
      </text>
      <box height={1} />
      <text fg={theme.textMuted}>{keyHint(enterVerb(row().kind), "close")}</text>
    </Modal>
  )
}
