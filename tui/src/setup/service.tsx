// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Background service: its state, and installing or removing it. */
import { getService, installService, removeService } from "../lib/api"
import { installNotes, serviceView } from "../lib/service"
import type { Flow } from "./flow"

export function createService({ host, dialog, load, once, choose, say }: Flow) {
  function service() {
    return load("Background service", getService(), showService)
  }

  function showService(state: Awaited<ReturnType<typeof getService>>) {
    const { headline, detail, action } = serviceView(state)
    if (action === "none") return say("Background service", [headline, detail], () => dialog.close())
    choose(
      "Background service",
      [
        { name: headline, description: "", value: "", disabled: true },
        ...(action === "install"
          ? [{ name: "Keep running in the background…", description: detail, value: "install" }]
          : [
              { name: "Keep it running", description: "", value: "keep" },
              { name: "Remove the background service", description: detail, value: "remove" },
            ]),
      ],
      (v) => {
        if (v === "keep") return void dialog.close()
        if (v === "install") return confirmInstall(state.copies ?? [])
        if (state.managed) {
          return say(
            "Background service",
            ["This backend is the service. Remove it from a shell:", "trading-r1 daemon uninstall"],
            service,
          )
        }
        void once(async () => {
          const r = await removeService()
          host.notify(r.ok ? "Background service removed" : `Not removed: ${r.detail}`)
          await service()
        })
      },
      () => dialog.close(),
      "select",
      "close",
      1, // past the headline
    )
  }

  function confirmInstall(copies: string[]) {
    say("Keep running in the background?", installNotes(copies), () =>
      choose(
        "Install the background service?",
        [
          { name: "Not now", description: "", value: "no" },
          { name: "Install it", description: "", value: "yes" },
        ],
        (v) => {
          if (v !== "yes") return void service()
          void once(async () => {
            const r = await installService()
            host.notify(r.ok ? `Background service installed: ${r.value.note}` : `Not installed: ${r.detail}`)
            await service()
          })
        },
        service,
      ),
    )
  }

  return { service }
}
