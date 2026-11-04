// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * DialogSelect: one clarifying question as a modal: a native `<select>` over the
 * suggested options, plus an optional "type your own" row that swaps in an input.
 *
 * Native widgets do the keyboard work (↑/↓/enter on select; enter on input);
 * escape cancels the whole flow. `onSubmit("")` means "skip this one".
 */
import { Show, createSignal, onCleanup } from "solid-js"

import { useKeyboard } from "@opentui/solid"

import { type AskQuestion } from "../lib/api"
import { FREE_TEXT_VALUE, buildOptions } from "../lib/ask-user"
import { keyHint } from "../lib/hints"
import { layout, selectColors, theme } from "../theme"
import { Field } from "./field"
import { Modal } from "./modal"

export function DialogSelect(props: {
  q: AskQuestion
  index: number
  total: number
  onSubmit: (answer: string) => void
  onCancel: () => void
  deadlineMs?: number // when set, show a countdown and auto-pick the recommended on expiry
  onTimeout?: () => void
}) {
  const [freeText, setFreeText] = createSignal(false)
  const options = () => buildOptions(props.q)

  // Countdown, then pick the recommended answer.
  const [remaining, setRemaining] = createSignal(
    props.deadlineMs ? Math.max(0, Math.ceil((props.deadlineMs - Date.now()) / 1000)) : 0,
  )
  if (props.deadlineMs) {
    const t = setInterval(() => {
      const secs = Math.max(0, Math.ceil((props.deadlineMs! - Date.now()) / 1000))
      setRemaining(secs)
      if (secs <= 0) {
        clearInterval(t)
        props.onTimeout?.()
      }
    }, 1000)
    onCleanup(() => clearInterval(t))
  }

  useKeyboard((e) => {
    if (e.name === "escape") props.onCancel()
  })

  return (
    <Modal width={layout.width.lg} title={`${props.q.header}  (${props.index + 1}/${props.total})`}>
      <text fg={theme.text}>{props.q.question}</text>
      <box height={1} />
      <Show
        when={!freeText()}
        fallback={
          <Field>
            <input
              focused
              placeholder="type your answer / enter to confirm / esc to cancel"
              onSubmit={(v: string | object) => props.onSubmit(typeof v === "string" ? v.trim() : "")}
            />
          </Field>
        }
      >
        <select
          focused
          options={options()}
          height={Math.min(options().length * 2, 12)}
          showDescription
          showScrollIndicator={options().length > 6}
          wrapSelection
          {...selectColors}
          onSelect={(_i, opt) => {
            const value = (opt as { value?: string } | null)?.value ?? ""
            if (value === FREE_TEXT_VALUE) setFreeText(true)
            else props.onSubmit(value)
          }}
        />
      </Show>
      <box height={1} />
      <text fg={theme.textMuted}>{keyHint("select", "cancel")}</text>
      <Show when={props.deadlineMs}>
        <text fg={theme.accent}>using recommended in {remaining()}s …</text>
      </Show>
    </Modal>
  )
}
