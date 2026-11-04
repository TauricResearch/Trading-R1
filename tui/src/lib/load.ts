// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * "Load, then show a dialog": the step that fetches what a dialog lists, and what the person is
 * told when it fails. One wording for every dialog.
 */
import { errorText } from "./api"

/** `load(title, step, then)` runs `then` with what `step` fetched, or reports its failure under
 *  `title`. `stamp` tells the screen that asked: if it has changed by the time the step ends
 *  (the person went elsewhere), neither the result nor the failure is shown. */
export function loader(report: (title: string, lines: string[]) => void, stamp: () => unknown = () => 0) {
  return async function load<T>(title: string, step: Promise<T>, then: (value: T) => void): Promise<void> {
    const mine = stamp()
    let value: T
    try {
      value = await step
    } catch (e) {
      if (stamp() === mine) report(title, [`Unavailable: ${errorText(e)}`])
      return
    }
    if (stamp() === mine) then(value)
  }
}
