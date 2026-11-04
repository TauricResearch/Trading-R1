// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * InfoDialog: a small read-only modal: a title + lines of text. Esc/enter closes.
 * Used for /status, and for command results/confirmations (compact, copy, export).
 */
import { For } from "solid-js"

import { useKeyboard } from "@opentui/solid"

import { keyHint } from "../lib/hints"
import { layout, theme } from "../theme"
import { Modal } from "./modal"

export function InfoDialog(props: {
  title: string
  lines: string[]
  onClose: () => void
  width?: number // defaults to sm (terse messages); rich detail passes a wider one
  escape?: "close" | "back" // a detail reached from a list goes back to it
}) {
  useKeyboard((e) => {
    if (e.name === "escape" || e.name === "return" || e.name === "enter") props.onClose()
  })
  return (
    <Modal width={props.width ?? layout.width.sm} title={props.title}>
      <For each={props.lines}>
        {(l) => (
          <text fg={theme.text} wrapMode="word">
            {l}
          </text>
        )}
      </For>
      <box height={1} />
      <text fg={theme.textMuted}>{keyHint(null, props.escape ?? "close", false)}</text>
    </Modal>
  )
}
