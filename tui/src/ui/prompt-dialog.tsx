// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * PromptDialog: a modal with a single free-text input. Enter submits the (trimmed)
 * value, esc cancels. Used by /compact to take an optional "what to preserve"
 * instruction; submitting empty is allowed (a plain compact).
 */
import { useKeyboard } from "@opentui/solid"

import { keyHint } from "../lib/hints"
import { layout, theme } from "../theme"
import { Field } from "./field"
import { Modal } from "./modal"

export function PromptDialog(props: {
  title: string
  prompt: string
  placeholder?: string
  onSubmit: (value: string) => void
  onCancel: () => void
}) {
  useKeyboard((e) => {
    if (e.name === "escape") props.onCancel()
  })
  return (
    <Modal width={layout.width.md} title={props.title}>
      <text fg={theme.text}>{props.prompt}</text>
      <box height={1} />
      <Field>
        <input
          focused
          placeholder={props.placeholder ?? ""}
          onSubmit={(v: string | object) => props.onSubmit(typeof v === "string" ? v.trim() : "")}
        />
      </Field>
      <box height={1} />
      <text fg={theme.textMuted}>{keyHint("confirm", "cancel", false)}</text>
    </Modal>
  )
}
