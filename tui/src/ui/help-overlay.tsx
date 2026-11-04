// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * HelpOverlay: a static modal of keybindings + usage tips. Esc/enter closes.
 */
import { For } from "solid-js"

import { useKeyboard } from "@opentui/solid"

import { keyHint } from "../lib/hints"
import { layout, theme } from "../theme"
import { Modal } from "./modal"

const KEYS: [string, string][] = [
  ["enter", "send the question"],
  ["^J", "new line in the question"],
  ["up / down", "recall an earlier question"],
  ["/ or ^K", "open the command palette"],
  ["shift+tab", "cycle the mode"],
  ["^N", "new session"],
  ["pgup / pgdn", "scroll the transcript"],
  ["esc", "stop the running turn; leave a dialog"],
  ["^C", "quit"],
]

const TIPS = [
  "Modes: auto (full toolkit), research (web first), analytical (numbers first).",
  "Ask a follow-up to keep working in the same session; resume an earlier one from the palette.",
  "Settings are saved as you change them, in ~/.trading-r1/settings.json.",
]

export function HelpOverlay(props: { onClose: () => void }) {
  useKeyboard((e) => {
    if (e.name === "escape" || e.name === "return" || e.name === "enter") props.onClose()
  })

  return (
    <Modal width={layout.width.md} title="Help">
      <text fg={theme.accent}>Keys</text>
      <For each={KEYS}>
        {([k, label]) => (
          <box flexDirection="row">
            <text fg={theme.primary}>{k.padEnd(14)}</text>
            <text fg={theme.text}>{label}</text>
          </box>
        )}
      </For>
      <box height={1} />
      <text fg={theme.accent}>Tips</text>
      <For each={TIPS}>{(t) => <text fg={theme.textMuted}>{t}</text>}</For>
      <box height={1} />
      <text fg={theme.textMuted}>{keyHint(null, "close", false)}</text>
    </Modal>
  )
}
