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

import { fitColumns } from "../lib/format"
import { type EscapeMeans, keyHint } from "../lib/hints"
import { layout, selectColors, theme } from "../theme"
import { Modal } from "./modal"

export interface ListOption {
  name: string // the row label
  description: string // the muted sub-label (the native <select> requires it)
  value: string
  disabled?: boolean
}

/** The columns a row's text gets in a select inside a modal `width` wide: the border, the
 *  inset, the selection marker and the scroll bar take the rest. */
export function rowColumns(width: number): number {
  return width - 2 - 2 * layout.pad.modal.x - 3 - 1
}

/** The options with their name and description cut to the row, ending with "…" when cut. */
export function fitRows<T extends { name: string; description: string }>(options: T[], width: number): T[] {
  const cols = rowColumns(width)
  return options.map((o) => ({
    ...o,
    name: fitColumns(o.name, cols),
    description: fitColumns(o.description, cols),
  }))
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
  const width = () => props.width ?? layout.width.md
  return (
    <Modal width={width()} title={props.title}>
      <select
        focused
        options={fitRows(props.options, width())}
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
