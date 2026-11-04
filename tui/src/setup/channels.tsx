// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Send channels: the list, a channel's actions, and the forms that add or edit one. */
import {
  type ChannelKind,
  type ChannelRow,
  deleteChannel,
  findTelegramChat,
  listChannelKinds,
  listChannels,
  putChannel,
  putCredential,
  testChannel,
} from "../lib/api"
import { pickFolder } from "../lib/folder-picker"
import { type Form, askedFields, newForm, serverErrors } from "../lib/form"
import { FormDialog } from "../ui/form-dialog"
import { InfoDialog } from "../ui/info-dialog"
import type { ListOption } from "../ui/list-dialog"
import { ADD, BACK, type Flow } from "./flow"

export function createChannels({ host, dialog, guarded, load, once, choose, say, nav }: Flow) {
  // where a new channel leads when it was added on the way to something else
  let afterAdd: (() => void) | null = null

  function channels() {
    afterAdd = null
    return load("Send channels", listChannels(), showChannels)
  }

  /** The channel just saved is ready: go on to what asked for it, else back to the list. */
  function added() {
    const then = afterAdd
    if (then) {
      afterAdd = null
      return void then()
    }
    void channels()
  }

  /** No channel yet: set one up, then go on. */
  function addThen(then: () => void) {
    host.notify("Reports need somewhere to go: add a channel")
    afterAdd = then
    void addChannel([])
  }

  function showChannels(data: Awaited<ReturnType<typeof listChannels>>) {
    const options: ListOption[] = [
      ...data.channels.map((c) => ({
        name: `${c.name} (${c.kind})`,
        description: c.problem ? `not usable: ${c.problem}` : (c.address ?? ""),
        value: c.name,
      })),
      { name: "Add a channel…", description: "email, Telegram or a folder", value: ADD },
    ]
    if (data.problems.settings) {
      return say("Send channels", [`The settings file cannot be used: ${data.problems.settings}`], () =>
        dialog.close(),
      )
    }
    choose(
      "Send channels",
      options,
      (v) =>
        v === ADD ? addChannel(data.channels) : channelActions(data.channels.find((c) => c.name === v)!),
      () => dialog.close(),
      "select",
      "close",
    )
  }

  function channelActions(c: ChannelRow) {
    choose(
      `${c.name} (${c.kind})`,
      [
        { name: "Send a test message", description: c.address ?? "fix the settings first", value: "test" },
        { name: "Edit…", description: "change its settings", value: "edit" },
        { name: "Remove", description: "stop sending there", value: "remove" },
      ],
      (v) => {
        if (v === "test") return void once(() => runTest(c))
        if (v === "edit") return editChannel(c)
        return void once(() => removeChannel(c))
      },
      channels,
    )
  }

  async function runTest(c: ChannelRow) {
    host.notify(`Sending a test to ${c.name}…`)
    await guarded(testChannel(c.name), (r) => {
      const lines = r.ok
        ? r.value.outcomes.map(
            (o) => `${o.channel}: ${o.status} (${o.address})${o.detail ? `: ${o.detail}` : ""}`,
          )
        : [r.detail]
      say("Test message", lines, () => channelActions(c))
    })
  }

  async function removeChannel(c: ChannelRow, force = false) {
    await guarded(deleteChannel(c.name, force), (r) => {
      if (r.ok) {
        host.notify(`${c.name} removed`)
        return void channels()
      }
      if (r.detail.includes("scheduled jobs")) {
        return choose(
          `Remove ${c.name}?`,
          [
            { name: "Keep it", description: r.detail, value: BACK },
            {
              name: "Remove it anyway",
              description: "those jobs will report it as not delivered",
              value: "force",
            },
          ],
          (v) => (v === "force" ? void once(() => removeChannel(c, true)) : channelActions(c)),
          () => channelActions(c),
        )
      }
      say("Remove", [r.detail], () => channelActions(c))
    })
  }

  /** A channel's secrets are named for the channel: the first of a kind keeps the plain name
   *  (`TR1_SMTP_PASSWORD`), a second is `TR1_SMTP_PASSWORD_WORK`, so adding a channel never
   *  replaces another's password. */
  function ownSecrets(kind: ChannelKind, channel: string): ChannelKind {
    if (channel === kind.name) return kind
    const suffix = "_" + channel.toUpperCase().replace(/[^A-Z0-9]/g, "_")
    return {
      ...kind,
      fields: kind.fields.map((f) =>
        f.type === "secret" && typeof f.default === "string" ? { ...f, default: f.default + suffix } : f,
      ),
    }
  }

  function uniqueName(kind: string, taken: ChannelRow[]): string {
    const names = new Set(taken.map((c) => c.name))
    if (!names.has(kind)) return kind
    let n = 2
    while (names.has(`${kind}-${n}`)) n++
    return `${kind}-${n}`
  }

  function addChannel(existing: ChannelRow[]) {
    return load("Add a channel", listChannelKinds(), (kinds) => pickKind(kinds, existing))
  }

  function pickKind(kinds: ChannelKind[], existing: ChannelRow[]) {
    choose(
      "Add a channel",
      kinds.map((k) => ({ name: k.label, description: k.summary, value: k.name })),
      (v) => {
        const name = uniqueName(v, existing)
        const kind = ownSecrets(kinds.find((k) => k.name === v)!, name)
        if (kind.name === "telegram") return addTelegram(kind, name)
        const presets = Object.keys(kind.presets)
        if (!presets.length) return channelForm(kind, name, {}, {})
        choose(
          `${kind.label} provider`,
          [
            ...presets.map((p) => ({ name: p, description: String(kind.presets[p].host ?? ""), value: p })),
            { name: "Other", description: "enter the server yourself", value: "" },
          ],
          (p) => channelForm(kind, name, {}, p ? kind.presets[p] : {}),
          () => addChannel(existing),
        )
      },
      channels,
    )
  }

  function editChannel(c: ChannelRow) {
    void load("Edit", listChannelKinds(), (kinds) => {
      const kind = kinds.find((k) => k.name === c.kind)
      if (!kind)
        return say("Edit", ["This kind of channel is not known to this version."], () => channelActions(c))
      const existing = {
        ...c.settings,
        ...Object.fromEntries(Object.entries(c.secrets).map(([f, s]) => [f, s.name])),
      }
      channelForm(kind, c.name, existing, {}, c)
    })
  }

  function channelForm(
    kind: ChannelKind,
    name: string,
    existing: Record<string, unknown>,
    preset: Record<string, unknown>,
    editing?: ChannelRow,
  ) {
    const form: Form = newForm(askedFields(kind.fields, preset), existing, preset)
    const configured = Object.fromEntries(
      Object.entries(editing?.secrets ?? {}).map(([f, s]) => [f, s.configured]),
    )
    dialog.show(() => (
      <FormDialog
        pickFolder={choosePath}
        title={editing ? `Edit ${name}` : `Add ${kind.label}`}
        intro={[kind.summary]}
        form={form}
        configured={configured}
        onCancel={() => (editing ? channelActions(editing) : channels())}
        onSubmit={async (p) => {
          const r = await putChannel(name, {
            kind: kind.name,
            settings: { ...preset, ...p.settings },
            secrets: p.secrets,
          })
          if (!r.ok) return serverErrors(r.detail, kind.fields)
          host.notify(`${name} saved`)
          if (r.value.problem) say("Saved, but not usable yet", [r.value.problem], channels)
          else added()
          return null
        }}
      />
    ))
  }

  /** Telegram: the bot's token, then the chat, found by the owner writing to the bot and saved
   *  only after they confirm it is them. */
  function addTelegram(kind: ChannelKind, name: string) {
    const tokenField = kind.fields.find((f) => f.type === "secret")!
    const form = newForm([tokenField])
    dialog.show(() => (
      <FormDialog
        title="Add Telegram"
        intro={[
          "Create a bot with @BotFather in Telegram and paste its token here.",
          "Next you will write one message to your bot so it can find your chat.",
        ]}
        form={form}
        onCancel={() => channels()}
        submitLabel="continue"
        onSubmit={async (p) => {
          const tokenEnv = String(p.settings[tokenField.name])
          const stored = await putCredential(tokenEnv, p.secrets[tokenField.name])
          if (!stored.ok) return { _: stored.detail }
          void findChat(kind, name, tokenEnv)
          return null
        }}
      />
    ))
  }

  async function findChat(kind: ChannelKind, name: string, tokenEnv: string) {
    let cancelled = false // esc stops waiting; the search itself ends on the server within a minute
    dialog.show(() => (
      <InfoDialog
        title="Add Telegram"
        lines={["Write any message to your bot in Telegram now.", "Waiting up to a minute…"]}
        escape="back"
        onClose={() => {
          cancelled = true
          void channels()
        }}
      />
    ))
    const mine = nav()
    const r = await findTelegramChat(tokenEnv, 60)
    if (cancelled || nav() !== mine) return
    if (!r.ok) return say("Add Telegram", [r.detail], channels)
    const chat = r.value.chat
    if (!chat)
      return say("Add Telegram", ["No message arrived. Try again once the bot is started."], channels)
    const who = chat.username ? `@${chat.username}` : (chat.first_name ?? "this chat")
    choose(
      "Is this you?",
      [
        { name: `Yes, send reports to ${who}`, description: `chat ${chat.id}`, value: "yes" },
        { name: "No", description: "do not save this chat", value: "no" },
      ],
      (v) => {
        if (v !== "yes") return void channels()
        void once(async () => {
          await guarded(
            putChannel(name, {
              kind: kind.name,
              settings: { token_env: tokenEnv, chat_id: chat.id },
              secrets: {},
            }),
            (saved) =>
              say(
                "Add Telegram",
                saved.ok ? ["Saved. Send a test from the list to try it."] : [saved.detail],
                saved.ok ? added : channels,
              ),
          )
        })
      },
      channels,
      "confirm",
    )
  }

  /** The system's folder chooser; null when there is none here or it was closed. */
  async function choosePath(): Promise<string | null> {
    const r = await pickFolder()
    return r.kind === "chosen" ? r.path : null
  }

  return { channels, addThen }
}
