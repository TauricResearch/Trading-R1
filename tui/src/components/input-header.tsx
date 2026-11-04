// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * InputHeader: the row directly above the input. Left: the active mode (colored
 * per mode) + a plain "(shift+tab to cycle)" hint. Right: `model: <name>` (the
 * backbone, marked offline if the service isn't ready).
 */
import { type RGBA } from "@opentui/core"

import { type Mode } from "../lib/mode"
import { theme } from "../theme"

const MODE_COLOR: Record<Mode, RGBA> = {
  auto: theme.primary, // teal, the general default
  research: theme.info, // blue, web-first
  analytical: theme.warning, // amber, numbers-first
}

export function InputHeader(props: { mode: Mode; model: string; ready: boolean }) {
  return (
    <box height={1} flexShrink={0} flexDirection="row" paddingLeft={1} paddingRight={1}>
      <text fg={MODE_COLOR[props.mode]}>{props.mode}</text>
      <text fg={theme.textMuted}> (shift+tab to cycle)</text>
      <box flexGrow={1} />
      <text fg={theme.textMuted}>model: </text>
      <text fg={theme.text}>
        {props.model}
        {props.ready ? "" : " (offline)"}
      </text>
    </box>
  )
}
