// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Backbone picker helpers: the status line and `/model` option builder for the model dialog. */
import type { ListOption } from "../ui/list-dialog"
import type { ModelInfo } from "./api"

/** The status line under a backbone's provider header: the served model, plus a parenthesized
 *  reason when it is not usable. A ready row is just the model name. */
export function statusLine(m: ModelInfo): string {
  if (m.ready) return m.model
  if (!m.configured) return `${m.model} (needs ${m.api_key_env})`
  if (m.reason === "key_refused") return `${m.model} (key refused)`
  if (m.reason === "offline:reader") return `${m.model} (reader offline)`
  return `${m.model} (offline)`
}

/** Build the `/model` picker options. The header is the provider/runtime (Tauric / OpenAI /
 *  Google / vLLM / Ollama); the model name + live status sit underneath. A dot marks the
 *  session-active model: the selection this session, else the server default. Only a
 *  *ready* backbone is selectable (disabled rows still explain what to fix on select). */
export function modelOptions(
  models: ModelInfo[],
  selected: string | undefined,
  serverDefault: string | null,
): ListOption[] {
  const active = selected ?? serverDefault ?? undefined
  return models.map((m) => ({
    name: `${m.name === active ? "●" : " "} ${m.provider_label}`,
    description: `  ${statusLine(m)}`, // aligned under the name, past the current-model dot
    value: m.name,
    disabled: !m.ready,
  }))
}

/** The backbone a session starts on, and whether it is the one the user supplied (a key they
 *  hold, a server they run) with nothing chosen before: that choice is then kept and announced.
 *  In order: the launch profile (respected even when offline), the saved /model choice, the
 *  supplied backbone, the first ready one, the server's default, the first. */
export function pickBackbone(
  models: ModelInfo[],
  pick: {
    launch?: string
    saved?: string | null
    serverDefault: string | null
    defaultReason: string | null
  },
): { chosen: ModelInfo; supplied: boolean } | null {
  if (!models.length) return null
  const named = (name: string | null | undefined) => (name ? models.find((x) => x.name === name) : undefined)
  const server = (reason: string) => (pick.defaultReason === reason ? named(pick.serverDefault) : undefined)
  const supplied = server("supplied")
  const chosen =
    named(pick.launch) ??
    server("env") ?? // TR1_PROFILE held by the backend
    named(pick.saved) ??
    server("file") ?? // the providers file's "default"
    supplied ??
    named(pick.serverDefault)
  if (!chosen) return null
  return { chosen, supplied: !named(pick.launch) && !named(pick.saved) && chosen === supplied }
}

/** Guidance shown when an unusable backbone is chosen: name the exact fix for *this*
 *  reason: set an API key (cloud; the app asks for it under "API keys…" and keeps it in a file
 *  only the user can read, or the environment variable works), or start the local
 *  server (vLLM/Ollama). */
export function unavailableGuidance(m: ModelInfo): string[] {
  if (!m.configured && m.api_key_env) {
    return [
      `${m.provider_label} (${m.model}) needs an API key.`,
      "",
      `Enter it under "API keys…" in the menu, or export ${m.api_key_env}=<your key>`,
      "before starting the app. Keys in the environment take precedence over stored ones.",
    ]
  }
  if (m.reason === "key_refused") {
    return [
      `${m.provider_label} (${m.model}) refused your API key.`,
      "",
      m.api_key_env
        ? `Check ${m.api_key_env} and replace it under "API keys…" if it is wrong. A key in the environment takes precedence over a stored one.`
        : "Check the key this endpoint expects.",
    ]
  }
  // configured but not answering, generic by kind: a local
  // server that isn't running/loaded, or an unreachable remote endpoint.
  const what = m.reason === "offline:reader" ? "page reader" : "endpoint"
  const hint =
    m.kind === "local"
      ? `Start the local ${m.provider_label} server and make sure ${m.model} is loaded, then retry.`
      : "Make sure the endpoint is reachable, then retry."
  return [`${m.provider_label} (${m.model}) is offline: its ${what} did not answer.`, "", hint]
}
