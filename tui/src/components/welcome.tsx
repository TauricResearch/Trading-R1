// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Welcome, the idle screen: wordmark + a centered ask box, vertically centered.
 * On submit the app swaps to the two-pane research view.
 */
import type { TextareaRenderable } from "@opentui/core"

import { type Mode } from "../lib/mode"
import { Composer } from "./composer"
import { InputHeader } from "./input-header"
import { Logo } from "./logo"

export function Welcome(props: {
  onSubmit: (q: string) => void
  mode: Mode
  model: string
  ready: boolean
  focused?: boolean
  onInput?: (value: string) => void
  inputRef?: (input: TextareaRenderable) => void
}) {
  return (
    <box flexGrow={1} alignItems="center" paddingLeft={2} paddingRight={2}>
      {/* 7:8 top:bottom: the block sits a touch above center. */}
      <box flexGrow={7} minHeight={0} />
      <Logo />
      {/* gap between the logo and the composer ~ the composer's height; the 7:8
          outer spacers keep the logo+composer block's vertical placement. */}
      <box height={3} flexShrink={0} />
      <box width="40%" minWidth={48} flexShrink={0} flexDirection="column">
        <InputHeader mode={props.mode} model={props.model} ready={props.ready} />
        <Composer
          onSubmit={props.onSubmit}
          focused={props.focused}
          onInput={props.onInput}
          inputRef={props.inputRef}
        />
      </box>
      <box flexGrow={8} minHeight={0} />
    </box>
  )
}
