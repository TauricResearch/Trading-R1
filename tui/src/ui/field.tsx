// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Field, the app's one text-entry shell: a rounded border that brightens while
 * focused, with the content inset a cell from it. Every input and textarea sits in one.
 */
import type { JSX } from "solid-js"

import { layout, theme } from "../theme"

export function Field(props: { focused?: boolean; children: JSX.Element }) {
  return (
    <box
      border
      borderStyle={layout.border}
      borderColor={(props.focused ?? true) ? theme.borderActive : theme.border}
      paddingLeft={layout.pad.field}
      paddingRight={layout.pad.field}
    >
      {props.children}
    </box>
  )
}
