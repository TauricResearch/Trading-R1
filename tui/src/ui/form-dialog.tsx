// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * FormDialog: a modal that edits a list of fields (as a destination kind declares them). One
 * field is focused at a time; tab, shift+tab and ↑↓ move, ←→ step a choice, enter goes on and
 * submits from the last field, ctrl+s submits from anywhere, esc cancels. A secret is typed into
 * an input drawn in the panel's own colour and shown as dots and a count, so it is never on the
 * screen. The server's messages land beside their fields.
 */
import { For, Show, createSignal, onCleanup } from "solid-js"

import { useKeyboard, useRenderer } from "@opentui/solid"

import {
  type Form,
  type Payload,
  check,
  cycleChoice,
  eraseSecret,
  masked,
  moveFocus,
  setValue,
  toPayload,
  typeSecret,
} from "../lib/form"
import { layout, theme } from "../theme"
import { Field } from "./field"
import { Modal } from "./modal"

export function FormDialog(props: {
  title: string
  intro?: string[]
  form: Form
  configured?: Record<string, boolean> // secrets already set: left blank, they are kept
  submitLabel?: string
  /** Send the payload; resolve to errors to show beside the fields, or null when it is done. */
  onSubmit: (payload: Payload) => Promise<Record<string, string> | null>
  onCancel: () => void
  /** Choose a folder with the system's own dialog; a path field offers it on ctrl+o. */
  pickFolder?: () => Promise<string | null>
}) {
  const [form, setForm] = createSignal(props.form)
  const [busy, setBusy] = createSignal(false)
  const current = () => form().fields[form().focus]

  const firstError = (errors: Record<string, string>) =>
    Math.max(
      0,
      form().fields.findIndex((f) => errors[f.name]),
    )

  async function submit() {
    if (busy()) return
    const local = check(form(), props.configured)
    if (Object.keys(local).length) return setForm({ ...form(), errors: local, focus: firstError(local) })
    setBusy(true)
    let remote: Record<string, string> | null
    try {
      remote = await props.onSubmit(toPayload(form()))
    } catch (e) {
      remote = { _: String(e) }
    }
    // done (null): the caller has moved on, and the form stays locked until it is replaced
    if (remote === null) return
    setBusy(false)
    setForm({ ...form(), errors: remote, focus: firstError(remote) })
  }

  const advance = () => {
    if (form().focus === form().fields.length - 1) void submit()
    else setForm(moveFocus(form(), 1))
  }
  // Enter on a choice moves on after the key has been delivered, or the next input would take
  // the same Enter as its own.
  const advanceAfterKey = () => setTimeout(advance, 0)

  // A secret is typed and pasted here, never into an input widget, so no renderable holds it.
  const renderer = useRenderer()
  const onPaste = (e: { bytes: Uint8Array }) => {
    if (busy() || current().type !== "secret") return
    setForm(typeSecret(form(), current().name, new TextDecoder().decode(e.bytes)))
  }
  renderer.keyInput.on("paste", onPaste)
  onCleanup(() => renderer.keyInput.off("paste", onPaste))

  useKeyboard((e) => {
    if (busy()) return
    if (e.name === "escape") props.onCancel()
    else if (e.ctrl && e.name === "s") void submit()
    else if (e.ctrl && e.name === "o" && current().type === "path" && props.pickFolder) {
      const name = current().name
      void props.pickFolder().then((p) => p && setForm((f) => setValue(f, name, p)))
    } else if (e.name === "backtab" || (e.name === "tab" && e.shift)) setForm(moveFocus(form(), -1))
    else if (e.name === "tab" || e.name === "down") setForm(moveFocus(form(), 1))
    else if (e.name === "up") setForm(moveFocus(form(), -1))
    else if (current().type === "choice") {
      if (e.name === "left") setForm(cycleChoice(form(), current().name, -1))
      else if (e.name === "right" || e.name === "space") setForm(cycleChoice(form(), current().name, 1))
      else if (e.name === "return" || e.name === "enter") advanceAfterKey()
    } else if (current().type === "secret") {
      const name = current().name
      if (e.name === "return" || e.name === "enter") advanceAfterKey()
      else if (e.name === "backspace") setForm(eraseSecret(form(), name))
      else if (e.ctrl && e.name === "u") setForm(eraseSecret(form(), name, true))
      else if (!e.ctrl && !e.meta && e.sequence.length >= 1 && e.sequence >= " " && e.sequence !== "\x7f")
        setForm(typeSecret(form(), name, e.sequence))
    }
  })

  return (
    <Modal width={layout.width.lg} title={props.title}>
      <For each={props.intro ?? []}>
        {(l) => (
          <text fg={theme.textMuted} wrapMode="word">
            {l}
          </text>
        )}
      </For>
      <Show when={form().errors._}>
        <text fg={theme.error} wrapMode="word">
          {form().errors._}
        </text>
      </Show>
      <For each={form().fields}>
        {(f, i) => {
          const focused = () => form().focus === i()
          const secret = f.type === "secret"
          return (
            <box flexDirection="column" marginTop={1}>
              <box flexDirection="row">
                <text fg={focused() ? theme.accent : theme.text}>{f.label + (f.required ? " *" : "")}</text>
                <Show when={secret && props.configured?.[f.name]}>
                  <text fg={theme.textMuted}>{"  (set; leave blank to keep it)"}</text>
                </Show>
              </box>
              <Show
                when={f.type === "text" || f.type === "int" || f.type === "list" || f.type === "path"}
                fallback={
                  <Show
                    when={secret}
                    fallback={
                      <text fg={focused() ? theme.text : theme.textMuted}>
                        {"‹ " + (form().values[f.name] || "-") + " ›"}
                      </text>
                    }
                  >
                    <Field focused={focused()}>
                      <text fg={theme.text}>{masked(form().values[f.name]) || (focused() ? "▏" : " ")}</text>
                    </Field>
                  </Show>
                }
              >
                <Field focused={focused()}>
                  <input
                    focused={focused() && !busy()}
                    value={form().values[f.name]}
                    placeholder={f.example}
                    onInput={(v: string) => setForm(setValue(form(), f.name, v))}
                    onSubmit={() => advance()}
                  />
                </Field>
              </Show>
              <Show when={form().errors[f.name]}>
                <text fg={theme.error}>{form().errors[f.name]}</text>
              </Show>
              <Show when={focused() && f.type === "path" && props.pickFolder}>
                <text fg={theme.textMuted}>ctrl+o choose with the system dialog</text>
              </Show>
              <Show when={focused() && f.help}>
                <text fg={theme.textMuted} wrapMode="word">
                  {f.help}
                </text>
              </Show>
            </box>
          )
        }}
      </For>
      <box height={1} />
      <text fg={theme.textMuted}>
        {busy()
          ? "working…"
          : form().fields.length === 1
            ? `enter ${props.submitLabel ?? "save"} / esc back`
            : `tab next / ctrl+s ${props.submitLabel ?? "save"} / esc cancel`}
      </text>
    </Modal>
  )
}
