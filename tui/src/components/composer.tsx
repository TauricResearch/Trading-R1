// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Composer: the question input. */
import { defaultTextareaKeyBindings, type TextareaRenderable } from "@opentui/core"

import { theme } from "../theme"
import { Field } from "../ui/field"

const PLACEHOLDER = "ask a finance question, or type / for commands"

// A `<textarea>` wraps to its width where `<input>` would horizontally scroll with a
// fixed scroll-off margin. The keymap is OpenTUI's standard one with one binding changed
// (`input_submit: "return"`): Enter submits instead of inserting a newline. Newline
// stays on Ctrl+J (terminal LF, OpenTUI "linefeed"); Ctrl+U/K/A/E, word-nav, arrows
// and undo are the defaults. Only `return` submits; keypad-Enter keeps the lib default.
const KEY_BINDINGS = defaultTextareaKeyBindings.map((b) =>
  b.name === "return" && b.action === "newline" ? { ...b, action: "submit" as const } : b,
)

export function Composer(props: {
  onSubmit: (q: string) => void
  placeholder?: string
  focused?: boolean
  onInput?: (value: string) => void
  inputRef?: (input: TextareaRenderable) => void
}) {
  // Local handle so the change/submit listeners can read the buffer (OpenTUI's
  // content/submit events carry no payload); also forwarded to the parent.
  let el: TextareaRenderable | undefined
  // The shared Field supplies the border + inset (the textarea child holds focus, so
  // we key the border off `focused` directly). No fixed height: it sits at one row
  // and grows up to a few lines for multi-line questions.
  return (
    <Field focused={props.focused}>
      <textarea
        ref={(r: TextareaRenderable) => {
          el = r
          props.inputRef?.(r)
        }}
        width="100%"
        minHeight={1}
        maxHeight={6}
        wrapMode="word"
        keyBindings={KEY_BINDINGS}
        focused={props.focused ?? true}
        placeholder={props.placeholder ?? PLACEHOLDER}
        placeholderColor={theme.textMuted}
        onContentChange={() => props.onInput?.(el?.plainText ?? "")}
        onSubmit={() => props.onSubmit(el?.plainText ?? "")}
      />
    </Field>
  )
}
