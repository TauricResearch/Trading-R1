// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Export report: where a session's report is saved. */
import { homedir } from "node:os"
import { saveReport } from "../lib/api"
import { pickFolder } from "../lib/folder-picker"
import { PromptDialog } from "../ui/prompt-dialog"
import type { Flow } from "./flow"

/** A path as a person writes it: the home folder as `~`. */
export function shortPath(path: string, home = homedir()): string {
  return path === home || path.startsWith(`${home}/`) ? `~${path.slice(home.length)}` : path
}

export function createReportExport({ host, dialog, once, choose, nav }: Flow) {
  /** Save a session's report as files: into the reports folder, or a folder the owner chooses
   *  (the system's dialog where there is one, else a name or path typed). */
  function exportReport(sessionId: string) {
    // A result is announced, never shown as a dialog: the owner may be somewhere else by then.
    const save = async (dir?: string) => {
      const mine = nav()
      const r = await saveReport(sessionId, dir)
      if (r.ok) return host.notify(`Report saved: ${shortPath(r.value.dir)}`)
      if (nav() === mine) host.info("Export failed", [r.detail])
      else host.notify(`Export failed: ${r.detail}`)
    }
    choose(
      "Export report",
      [
        {
          name: "Save to the reports folder",
          description: "the default; created if needed",
          value: "default",
        },
        {
          name: "Choose another folder…",
          description: "the system's dialog, or type a path",
          value: "other",
        },
      ],
      async (v) => {
        dialog.close()
        if (v === "default") return void once(() => save())
        const mine = nav()
        const picked = await pickFolder()
        if (nav() !== mine) return // the owner went elsewhere while the system dialog was open
        if (picked.kind === "chosen") return void once(() => save(picked.path))
        if (picked.kind === "cancelled") return
        dialog.show(() => (
          <PromptDialog
            title="Export report"
            prompt="Folder: a name inside the reports folder, or a full path (~ is your home)."
            placeholder="briefs"
            onCancel={() => dialog.close()}
            onSubmit={(text) => {
              dialog.close()
              void once(() => save(text || undefined))
            }}
          />
        ))
      },
      () => dialog.close(),
      "save",
      "cancel",
    )
  }

  return { exportReport }
}
