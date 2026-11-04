// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * What the setup screens share: every screen goes through this flow's dialog, so an answer that
 * arrives after the owner has moved on is dropped, and one write or test runs at a time.
 */
import type { EscapeMeans } from "../lib/hints"
import { loader } from "../lib/load"
import type { DialogApi } from "../ui/dialog"
import { InfoDialog } from "../ui/info-dialog"
import { type ListOption, ListDialog } from "../ui/list-dialog"

export interface SetupHost {
  dialog: DialogApi
  info: (title: string, lines: string[]) => void
  notify: (text: string) => void
  onKeysChanged?: () => void // the model list may have changed
  model?: () => string | undefined // the model in use; a scheduled report runs on it
}

export const BACK = "__back__"
export const ADD = "__add__"

export type Flow = ReturnType<typeof createFlow>

export function createFlow(host: SetupHost) {
  // Every screen goes through `show`/`close`, which count navigations. An answer that arrives after
  // the owner has moved on (esc, another choice) is dropped instead of opening a stale dialog.
  let nav = 0
  let working = false // a write or a test is in flight: repeated keys do not repeat it
  const dialog: DialogApi = {
    show: (n) => {
      nav++
      host.dialog.show(n)
    },
    close: () => {
      nav++
      host.dialog.close()
    },
    open: () => host.dialog.open(),
  }
  /** Run an async step for the screen that is up now; `then` runs only if it is still the one. */
  async function guarded<T>(step: Promise<T>, then: (value: T) => void): Promise<void> {
    const mine = nav
    const value = await step
    if (nav === mine) then(value)
  }
  /** Fetch what a screen lists, then show it; a failure is told under the screen's title. */
  const load = loader(
    (title, lines) => host.info(title, lines),
    () => nav,
  )
  /** One write/test at a time: a second request while one is running is ignored. */
  async function once(run: () => Promise<void>): Promise<void> {
    if (working) return
    working = true
    try {
      await run()
    } finally {
      working = false
    }
  }

  function choose(
    title: string,
    options: ListOption[],
    onSelect: (v: string) => void,
    onCancel: () => void,
    confirm = "select",
    escape: EscapeMeans = "back",
    selected?: number,
  ) {
    dialog.show(() => (
      <ListDialog
        title={title}
        selected={selected}
        options={options}
        confirm={confirm}
        escape={escape}
        onCancel={onCancel}
        onSelect={(v) => onSelect(v)}
      />
    ))
  }

  function say(title: string, lines: string[], then: () => void) {
    // Enter closes the note; the next screen opens after that key has been delivered, or it would
    // take the same Enter as a choice of its own.
    dialog.show(() => (
      <InfoDialog title={title} lines={lines} escape="back" onClose={() => setTimeout(then, 0)} />
    ))
  }

  return { host, dialog, guarded, load, once, choose, say, nav: () => nav }
}
