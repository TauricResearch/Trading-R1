// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Model keys: which are set and where, and the forms that store them write-only. */
import { type Credential, deleteCredential, listCredentials, putCredential } from "../lib/api"
import { newForm } from "../lib/form"
import { FormDialog } from "../ui/form-dialog"
import type { ListOption } from "../ui/list-dialog"
import type { Flow } from "./flow"

export function createKeys({ host, dialog, load, once, choose, say }: Flow) {
  const where = (c: Credential) =>
    !c.configured
      ? "not set"
      : c.source === "env"
        ? "set in the environment" + (c.shadows_store ? " (overrides the stored one)" : "")
        : "stored"

  function apiKeys() {
    return load("API keys", listCredentials(), showKeys)
  }

  function showKeys(data: Awaited<ReturnType<typeof listCredentials>>) {
    const options: ListOption[] = data.credentials.map((c) => ({
      name: `${c.label} / ${c.name}`,
      description: where(c),
      value: c.name,
    }))
    const lines = data.problem ? [`The key store cannot be used: ${data.problem}`] : []
    if (lines.length) return say("API keys", lines, () => dialog.close())
    choose(
      "API keys",
      options,
      (name) => {
        const c = data.credentials.find((x) => x.name === name)!
        c.source === "store" || c.shadows_store ? keyActions(c) : enterKey(c)
      },
      () => dialog.close(),
      "set a key",
      "close",
    )
  }

  function keyActions(c: Credential) {
    choose(
      c.label,
      [
        { name: "Replace the key…", description: "type a new one", value: "replace" },
        {
          name: "Remove the stored key",
          description: "the environment, if set, still applies",
          value: "remove",
        },
      ],
      (v) => {
        if (v === "replace") return enterKey(c)
        void once(async () => {
          const r = await deleteCredential(c.name)
          host.onKeysChanged?.()
          say(c.label, [r.ok ? "The stored key was removed." : r.detail], apiKeys)
        })
      },
      apiKeys,
    )
  }

  function enterKey(c: Credential) {
    const form = newForm([
      {
        name: "value",
        label: `${c.label} key`,
        type: "secret",
        required: true,
        default: c.name,
        choices: [],
        minimum: null,
        maximum: null,
        example: "",
        help: "Stored in a file only you can read.",
      },
    ])
    dialog.show(() => (
      <FormDialog
        title={`${c.label} API key`}
        intro={[
          `Stored as ${c.name} in your private state folder, readable by you alone.`,
          ...(c.source === "env" ? ["Values exported in the environment still take precedence."] : []),
        ]}
        form={form}
        onCancel={apiKeys}
        onSubmit={async (p) => {
          const r = await putCredential(c.name, p.secrets.value)
          if (!r.ok) return { _: r.detail }
          host.onKeysChanged?.()
          host.notify(`${c.label} key stored`)
          void apiKeys()
          return null
        }}
      />
    ))
  }

  /** The key for a model chosen without one, in place: saved, then checked and used. */
  function keyFor(
    m: { provider_label: string; model: string; api_key_env: string },
    after: { use: () => Promise<string | null>; back: () => void },
  ) {
    const form = newForm([
      {
        name: "value",
        label: "API key",
        type: "secret",
        required: true,
        default: m.api_key_env,
        choices: [],
        minimum: null,
        maximum: null,
        example: "",
        help: "",
      },
    ])
    dialog.show(() => (
      <FormDialog
        title={`${m.provider_label} API key`}
        intro={[`${m.model} needs it. Stored on this machine, readable by you alone.`]}
        form={form}
        submitLabel="save and use"
        onCancel={after.back}
        onSubmit={async (p) => {
          const r = await putCredential(m.api_key_env, p.secrets.value)
          if (!r.ok) return { value: r.detail }
          host.onKeysChanged?.()
          if (r.value.shadows_store) return { value: `${m.api_key_env} in your environment takes precedence` }
          const refused = await after.use()
          return refused ? { value: refused } : null
        }}
      />
    ))
  }

  return { apiKeys, keyFor }
}
