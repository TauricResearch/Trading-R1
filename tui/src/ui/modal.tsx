// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Modal, the app's one floating-surface shell: a rounded, titled panel above the
 * workspace with a consistent inset and panel fill. Dialogs supply a width from the
 * layout scale and a title.
 */
import type { JSX } from "solid-js"

import { layout, theme } from "../theme"

export function Modal(props: { width: number; title: string; children: JSX.Element }) {
  return (
    <box
      width={props.width}
      border
      borderStyle={layout.border}
      title={props.title}
      borderColor={theme.primary}
      titleColor={theme.primary}
      backgroundColor={theme.backgroundPanel}
      flexDirection="column"
      paddingTop={layout.pad.modal.y}
      paddingBottom={layout.pad.modal.y}
      paddingLeft={layout.pad.modal.x}
      paddingRight={layout.pad.modal.x}
    >
      {props.children}
    </box>
  )
}
