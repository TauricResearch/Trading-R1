// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Dialog stack: a single-slot modal overlay.
 *
 * `DialogProvider` holds the active modal node; `useDialog().show(node)` raises
 * one and `.close()` dismisses it. `DialogHost` renders the active node centered
 * over a dim full-screen backdrop (absolute + high zIndex), so it floats above
 * the panes without disturbing their layout. `open()` lets callers (e.g. the
 * composer) yield keyboard focus while a modal is up.
 */
import { type JSX, createContext, createSignal, useContext } from "solid-js"
import { Show } from "solid-js"
import { RGBA } from "@opentui/core"

import { useTerminalDimensions } from "@opentui/solid"

type DialogNode = () => JSX.Element

export interface DialogApi {
  show: (node: DialogNode) => void
  close: () => void
  open: () => boolean
}

const DialogContext = createContext<DialogApi>()

export function useDialog(): DialogApi {
  const ctx = useContext(DialogContext)
  if (!ctx) throw new Error("useDialog must be used within a DialogProvider")
  return ctx
}

export function DialogProvider(props: { children: JSX.Element }) {
  const [node, setNode] = createSignal<DialogNode | null>(null)
  const api: DialogApi = {
    // store the thunk itself (updater form, else createSignal would call it)
    show: (n) => setNode(() => n),
    close: () => setNode(null),
    open: () => node() !== null,
  }
  return (
    <DialogContext.Provider value={api}>
      {props.children}
      <DialogHost node={node} />
    </DialogContext.Provider>
  )
}

/** The overlay layer: dim backdrop + centered active node. */
function DialogHost(props: { node: () => DialogNode | null }) {
  const dims = useTerminalDimensions()
  return (
    <Show when={props.node()} keyed>
      {(n: DialogNode) => (
        <box
          position="absolute"
          left={0}
          top={0}
          width={dims().width}
          height={dims().height}
          zIndex={1000}
          alignItems="center"
          justifyContent="center"
          backgroundColor={RGBA.fromInts(0, 0, 0, 160)}
        >
          {n()}
        </box>
      )}
    </Show>
  )
}
