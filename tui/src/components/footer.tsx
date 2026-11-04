// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Footer: the bottom status strip below the input.
 *
 * Left: the always-available key hints. Right: the version. The
 * mode + its cycle hint and the backbone model live in the InputHeader above the
 * input; live run status renders inline in the transcript. Padding matches the
 * InputHeader so the hints line up under the mode.
 */
import { Show } from "solid-js"

import { type ContextMeter } from "../lib/context"
import { theme } from "../theme"

function Hint(props: { k: string; label: string }) {
  return (
    <box flexDirection="row">
      <text fg={theme.accent}>{props.k}</text>
      <text fg={theme.textMuted}> {props.label}</text>
    </box>
  )
}

export function Footer(props: { version: string; notice?: string; context?: ContextMeter }) {
  return (
    <box height={1} flexDirection="row" paddingLeft={2} paddingRight={2} gap={2} flexShrink={0}>
      <Show
        when={props.notice}
        fallback={
          <>
            <Hint k="/" label="commands" />
            <Hint k="^N" label="new" />
            <Hint k="^C" label="quit" />
          </>
        }
      >
        <text fg={theme.accent}>{props.notice}</text>
      </Show>
      <box flexGrow={1} />
      <Show when={props.context}>
        <text fg={props.context!.warn ? theme.warning : theme.textMuted}>
          {props.context!.usage}. {props.context!.compaction}
        </text>
      </Show>
      <text fg={theme.textMuted}>v{props.version}</text>
    </box>
  )
}
