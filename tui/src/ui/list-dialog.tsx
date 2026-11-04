// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * ListDialog: a modal picker over a list of options (native `<select>`). Used by
 * /model, /sessions and the tools/skills browser. Each option carries a `value`;
 * `onSelect` gets it, esc cancels. A `disabled` value is not a normal selection:
 * by default it is ignored (e.g. a section header), but if `onSelectDisabled` is
 * given it routes there instead (e.g. a model that needs an API key shows guidance).
 */
import { useKeyboard } from "@opentui/solid"

import { type EscapeMeans, keyHint } from "../lib/hints"
import { layout, selectColors, theme } from "../theme"
import { Modal } from "./modal"

export interface ListOption {
  name: string // the row label
  description: string // the muted sub-label (the native <select> requires it)
  value: string
  disabled?: boolean
}

export function ListDialog(props: {
  title: string
  options: ListOption[]
  onSelect: (value: string) => void
  onCancel: () => void
  onSelectDisabled?: (value: string) => void
  width?: number // defaults to md; a description-rich list (tools/skills) passes lg
  selected?: number // the row the cursor starts on
  confirm?: string // what enter does (select, open, …)
  escape?: EscapeMeans // what esc does here
}) {
  useKeyboard((e) => {
    if (e.name === "escape") props.onCancel()
  })
  // each option renders on two lines (name + description), so size for that;
  // otherwise only the first row shows and the rest need scrolling to reveal.
  const height = () => Math.min(Math.max(props.options.length * 2, 2), 14)
  return (
    <Modal width={props.width ?? layout.width.md} title={props.title}>
      <select
        focused
        options={props.options}
        selectedIndex={props.selected ?? 0}
        height={height()}
        showDescription
        showScrollIndicator={props.options.length > 10}
        wrapSelection
        {...selectColors}
        onSelect={(_i, opt) => {
          const o = opt as ListOption | null
          if (!o) return
          if (o.disabled) {
            props.onSelectDisabled?.(o.value ?? "")
            return
          }
          props.onSelect(o.value ?? "")
        }}
      />
      <box height={1} />
      <text fg={theme.textMuted}>{keyHint(props.confirm ?? "select", props.escape ?? "cancel")}</text>
    </Modal>
  )
}
