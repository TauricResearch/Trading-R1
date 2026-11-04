// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Spinner: an animated braille frame + elapsed seconds, for live "working" feedback. */
import { type RGBA } from "@opentui/core"
import { createSignal, onCleanup } from "solid-js"

import { theme } from "../theme"

const FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

/** Animated braille glyph (char + trailing space) for an inline "running" prefix. */
export function SpinnerGlyph(props: { color?: RGBA }) {
  const [i, setI] = createSignal(0)
  const t = setInterval(() => setI((v) => (v + 1) % FRAMES.length), 100)
  onCleanup(() => clearInterval(t))
  return <text fg={props.color ?? theme.accent}>{FRAMES[i()]} </text>
}

export function Spinner(props: { label?: string; color?: RGBA }) {
  const [i, setI] = createSignal(0)
  const start = Date.now()
  const [elapsed, setElapsed] = createSignal(0)
  const t = setInterval(() => {
    setI((v) => (v + 1) % FRAMES.length)
    setElapsed(Math.floor((Date.now() - start) / 1000))
  }, 100)
  onCleanup(() => clearInterval(t))
  return (
    <box flexDirection="row" flexShrink={0}>
      <text fg={props.color ?? theme.accent}>{FRAMES[i()]} </text>
      <text fg={theme.textMuted}>
        {props.label ?? ""}
        {elapsed() > 0 ? ` ${elapsed()}s` : ""}
      </text>
    </box>
  )
}
