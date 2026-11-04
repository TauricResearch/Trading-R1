// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Session sync: copying the sessions on this machine, and restoring one from your account. */
import { importSessions, remoteSessions, restoreSession } from "../lib/api"
import { remoteOption } from "../lib/sync"
import type { Flow } from "./flow"

export function createSync({ host, dialog, load, once, choose, say }: Flow) {
  function syncMore() {
    choose(
      "Copy or restore sessions",
      [
        {
          name: "Copy the sessions already on this machine",
          description: "scheduled and Telegram sessions are left out",
          value: "import",
        },
        {
          name: "Restore a session from your account…",
          description: "one that is not on this machine",
          value: "restore",
        },
      ],
      (v) => {
        if (v === "restore") return void load("Restore a session", remoteSessions(), showRemote)
        void once(async () => {
          const r = await importSessions()
          host.notify(r.ok ? `Copying ${r.value.enrolled} existing session(s)` : `Not copied: ${r.detail}`)
          dialog.close()
        })
      },
      () => dialog.close(),
      "select",
      "close",
    )
  }

  function showRemote(sessions: Awaited<ReturnType<typeof remoteSessions>>) {
    if (!sessions.length) {
      return say(
        "Restore a session",
        ["All the sessions in your account are already on this machine."],
        syncMore,
      )
    }
    choose(
      "Restore a session",
      sessions.map(remoteOption),
      (id) =>
        void once(async () => {
          const r = await restoreSession(id)
          host.notify(r.ok ? "Restored: find it under Resume session" : `Not restored: ${r.detail}`)
          dialog.close()
        }),
      syncMore,
    )
  }

  return { syncMore }
}
