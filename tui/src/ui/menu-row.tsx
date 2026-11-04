// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * MenuRow: one row of a menu: the label on the left, a dim value (or a key, or the reason an
 * item is unavailable) right-aligned. The row under the cursor is inverted. Palette and Settings
 * share it.
 */
import { Show } from "solid-js"

import { theme } from "../theme"

export function MenuRow(props: { label: string; value?: string; selected: boolean; dim?: boolean }) {
  const fg = () => (props.selected ? theme.background : props.dim ? theme.textMuted : theme.text)
  return (
    <box width="100%" flexDirection="row" backgroundColor={props.selected ? theme.primary : undefined}>
      <text fg={fg()}>{"  " + props.label}</text>
      <box flexGrow={1} />
      <Show when={props.value}>
        <text fg={props.selected ? theme.background : theme.textMuted}>{props.value + " "}</text>
      </Show>
    </box>
  )
}
