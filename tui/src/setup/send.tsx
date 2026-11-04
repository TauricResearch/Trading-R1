// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Send report: the current report to the channels the owner picks, by name. */
import { type ChannelRow, listChannels, sendReport as postSend } from "../lib/api"
import { failureLine, summarize } from "../lib/send"
import type { ListOption } from "../ui/list-dialog"
import type { Flow } from "./flow"

const ALL = "\0all"

export function createSend(
  { host, dialog, load, once, choose, say, nav }: Flow,
  addChannel: (then: () => void) => void,
) {
  function sendReport(sessionId: string) {
    return load("Send report", listChannels(), (data) => {
      if (!data.channels.length) return addChannel(() => void sendReport(sessionId))
      pick(sessionId, data.channels)
    })
  }

  function pick(sessionId: string, channels: ChannelRow[]) {
    const usable = channels.filter((c) => !c.problem)
    const all = usable.map((c) => c.name)
    const options: ListOption[] = [
      ...(usable.length > 1 ? [{ name: "All channels", description: all.join(", "), value: ALL }] : []),
      ...channels.map((c) => ({
        name: c.name,
        description: c.problem ? `not usable: ${c.problem}` : c.kind,
        value: c.name,
      })),
    ]
    choose(
      "Send report",
      options,
      (v) => {
        const chosen = channels.find((c) => c.name === v)
        if (chosen?.problem) {
          return say(chosen.name, [`Not usable: ${chosen.problem}`, "Fix it under Send channels."], () =>
            pick(sessionId, channels),
          )
        }
        send(sessionId, v === ALL ? all : [v], true)
      },
      () => dialog.close(),
      "send",
      "cancel",
    )
  }

  /** One send. `onlyOnce`: not to a channel this report already went to. A result that arrives
   *  after the owner moved on is announced, not opened over what they are doing. */
  function send(sessionId: string, names: string[], onlyOnce: boolean) {
    dialog.close()
    const mine = nav()
    host.notify(`Sending to ${names.join(", ")}…`)
    void once(async () => {
      const r = await postSend(sessionId, names, onlyOnce)
      const here = nav() === mine
      if (!r.ok) {
        if (!here) return host.notify(`The report was not sent: ${r.detail}`)
        return retry(sessionId, names, r.detail, onlyOnce)
      }
      const { sent, already, failed, unsure } = summarize(r.value.outcomes)
      const done = sent.length ? `Report sent to ${sent.join(", ")}` : ""
      if (!failed.length && !unsure.length && !already.length) return host.notify(done)
      if (done) host.notify(done)
      if (!here) {
        const left = [...failed, ...unsure].map((o) => o.channel)
        if (left.length) host.notify(`Not confirmed for ${left.join(", ")}: send again from the palette`)
        return
      }
      if (failed.length) {
        return retry(
          sessionId,
          failed.map((o) => o.channel),
          failed.map(failureLine).join("; "),
          onlyOnce,
        )
      }
      const again = [...already, ...unsure.map((o) => o.channel)]
      const leave = unsure.length
        ? `${unsure.map((o) => o.channel).join(", ")} did not confirm; it may have gone out`
        : `this report already went to ${already.join(", ")}`
      choose(
        unsure.length ? "Send report" : "Already sent",
        [
          { name: "Send again", description: `a second copy to ${again.join(", ")}`, value: "again" },
          { name: "Leave it", description: leave, value: "leave" },
        ],
        (v) => (v === "again" ? send(sessionId, again, false) : dialog.close()),
        () => dialog.close(),
        "select",
        "close",
      )
    })
  }

  function retry(sessionId: string, names: string[], why: string, onlyOnce: boolean) {
    choose(
      "Not sent",
      [
        { name: "Try again", description: why, value: "retry" },
        { name: "Leave it", description: "nothing more is sent", value: "leave" },
      ],
      (v) => (v === "retry" ? send(sessionId, names, onlyOnce) : dialog.close()),
      () => dialog.close(),
      "select",
      "close",
    )
  }

  return { sendReport }
}
