// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Trading-R1 API client, framework-agnostic.
 *
 * Thin client over the `/research` (SSE) + `/health` endpoints. `researchStream`
 * is an async generator over typed SSE events; the last is always `done` (with
 * the trajectory) or `error`. `fetchImpl` is injectable for tests.
 */

import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import type { AskOption, AskQuestion, Citation, SSEEvent, Trajectory, Usage } from "./events.gen"

import type { Prefs } from "./settings"
import type { RemoteSession, SyncMark, SyncStatus } from "./sync"

export type { AskOption, AskQuestion, Citation, SSEEvent, Trajectory, Usage }

interface ResearchRequest {
  question: string
  mode?: string
  profile?: string
  sessionId?: string
}

/** An answer the user chose, sent back to resolve the suspended ask_user call. */
export interface AskAnswer {
  id: string
  question: string
  answer: string
}

export interface Health {
  status: "ok" | "degraded"
  version: string
  backbone: { profile: string; model: string; ready: boolean }
  // a backbone without a resolvable reader reports it as null
  reader: { profile: string | null; model: string | null; ready: boolean }
}

interface SessionSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
  turns: number
  sync?: SyncMark // the session's state in the archive
}

export interface StatusInfo {
  model: string
  context_window: number
  tokens_used: number
  used_pct: number
  compact_at: number | null // the token count at which the context compacts; null when off
  turns: number
}

export interface ModelInfo {
  name: string // stable id (tauric/gpt/anthropic/gemini/vllm/ollama)
  model: string // the served model, shown as the sub-line
  provider_label: string // menu header: Tauric / OpenAI / Google / vLLM / Ollama
  kind: "cloud" | "local"
  configured: boolean // credentials present (cloud key set, or keyless local)
  ready: boolean // usable right now: backbone AND paired reader answer
  reason: string // "ok" | "needs_key:<ENV>" | "key_refused" | "offline:backbone" | "offline:reader"
  api_key_env: string | null // the env var to set for a cloud backbone; null for keyless local
}

interface CompactResult {
  compacted: boolean
  reason?: string
  tokens_before?: number
  tokens_after?: number
}

type FetchImpl = typeof fetch
type ReqOpts = { baseUrl?: string; fetchImpl?: FetchImpl }

/** The bearer token the backend wants (set by the launcher), as request headers. */
function authHeaders(): Record<string, string> {
  const token = typeof process !== "undefined" ? process.env.TR1_API_TOKEN : undefined
  return token ? { authorization: `Bearer ${token}` } : {}
}

/** The address and token the backend currently publishes, or null. */
function published(): { url: string; token: string } | null {
  try {
    const home = process.env.TR1_HOME || join(homedir(), ".trading-r1")
    const meta = JSON.parse(readFileSync(join(home, "backend.json"), "utf8"))
    return typeof meta.url === "string" && typeof meta.token === "string" ? meta : null
  } catch {
    return null
  }
}

/** `fetch` for the backend. A backend that was restarted has a new token: on a 401 the token it
 *  published is adopted and the request is made once more. */
async function apiFetch(url: string | URL, init: RequestInit = {}): Promise<Response> {
  const resp = await fetch(url, init)
  const fresh = resp.status === 401 && typeof process !== "undefined" ? published() : null
  if (!fresh || fresh.token === process.env.TR1_API_TOKEN) return resp
  process.env.TR1_API_TOKEN = fresh.token
  process.env.TR1_API_URL = fresh.url
  const target = new URL(String(url))
  const base = new URL(fresh.url)
  target.protocol = base.protocol
  target.host = base.host
  return fetch(target, {
    ...init,
    headers: { ...(init.headers as Record<string, string>), ...authHeaders() },
  })
}

function apiUrl(): string {
  return (typeof process !== "undefined" && process.env.TR1_API_URL) || "http://127.0.0.1:8765"
}

export function parseFrame(frame: string): SSEEvent | null {
  let event = "message"
  const dataLines: string[] = []
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim()
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart())
  }
  if (dataLines.length === 0) return null
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) } as SSEEvent
  } catch {
    throw new Error(`malformed '${event}' event from the service`)
  }
}

/** What a failed call says, as a sentence: the server's own reason when it gave one. */
async function failure(resp: Response): Promise<string> {
  if (resp.status === 401) return "the backend refused this terminal's token; restart trading-r1"
  try {
    const data = (await resp.json()) as { detail?: unknown }
    if (typeof data.detail === "string") return data.detail
    if (Array.isArray(data.detail)) {
      const problems = data.detail as { loc?: unknown[]; msg?: string }[]
      return problems
        .map((d) => [(d.loc ?? []).slice(1).join("."), d.msg ?? ""].filter(Boolean).join(": "))
        .join("; ")
    }
  } catch {
    // the status is all there is
  }
  return `HTTP ${resp.status}`
}

/** The one place a call to the backend is made. A reply that is not 2xx throws an Error whose
 *  message is for the person (`errorText`); so does a backend that does not answer. Only an
 *  abort the caller asked for is rethrown as it is. */
async function request(
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
  opts: ReqOpts & { signal?: AbortSignal } = {},
): Promise<Response> {
  const doFetch = opts.fetchImpl ?? apiFetch
  let resp: Response
  try {
    resp = await doFetch(`${opts.baseUrl ?? apiUrl()}${path}`, {
      method,
      headers: { ...(body === undefined ? {} : { "content-type": "application/json" }), ...authHeaders() },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: opts.signal,
    })
  } catch (e) {
    if (opts.signal?.aborted) throw e
    throw new Error("the backend did not answer")
  }
  if (!resp.ok) throw new Error(await failure(resp))
  return resp
}

/** A failure as the sentence a dialog shows. */
export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** POST a JSON body and yield the SSE frames as typed events. `signal` aborts the
 * in-flight request (cancellation); the backend stops the run on disconnect. A stream that
 * closes before its `done` (or `error`) event is an interrupted run, and throws. */
async function* sseStream(
  path: string,
  body: unknown,
  opts: ReqOpts & { signal?: AbortSignal } = {},
): AsyncGenerator<SSEEvent> {
  const resp = await request("POST", path, body, opts)
  if (!resp.body) throw new Error("the backend sent no stream")
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  try {
    let buf = ""
    let finished = false
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        const ev = parseFrame(frame)
        if (ev) {
          finished ||= ev.event === "done" || ev.event === "error"
          yield ev
        }
      }
    }
    const tail = parseFrame(buf)
    if (tail) {
      finished ||= tail.event === "done" || tail.event === "error"
      yield tail
    }
    if (!finished) throw new Error("the connection closed before the run finished")
  } finally {
    // a frame we cannot read ends the stream early: release the connection, not just the loop
    await reader.cancel().catch(() => {})
  }
}

export function researchStream(
  req: ResearchRequest,
  opts: ReqOpts & { signal?: AbortSignal } = {},
): AsyncGenerator<SSEEvent> {
  return sseStream(
    "/research",
    {
      question: req.question,
      mode: req.mode ?? null,
      profile: req.profile ?? null,
      session_id: req.sessionId ?? null,
      stream: true,
    },
    opts,
  )
}

/** Reply to a mid-run `ask_user` question: resolves the suspended tool call so the
 *  streaming research run resumes with the user's answers. */
export async function answerAskUser(
  requestId: string,
  answers: AskAnswer[],
  opts: ReqOpts = {},
): Promise<{ resolved: boolean }> {
  return postJson<{ resolved: boolean }>("/answer", { request_id: requestId, answers }, opts)
}

async function getJson<T>(path: string, opts: ReqOpts = {}): Promise<T> {
  return (await (await request("GET", path, undefined, opts)).json()) as T
}

const postJson = async <T>(path: string, body: unknown, opts?: ReqOpts) =>
  (await (await request("POST", path, body, opts)).json()) as T
const putJson = async <T>(path: string, body: unknown, opts?: ReqOpts) =>
  (await (await request("PUT", path, body, opts)).json()) as T

export async function health(profile?: string, opts: ReqOpts = {}): Promise<Health> {
  const q = profile ? `?profile=${encodeURIComponent(profile)}` : ""
  return getJson<Health>(`/health${q}`, opts)
}

/** Saved sessions (most recently active first) for the /sessions picker. */
export async function listSessions(opts: ReqOpts = {}): Promise<SessionSummary[]> {
  return (await getJson<{ sessions: SessionSummary[] }>("/sessions", opts)).sessions
}

/** One entry of a session as the user saw it: a turn (its recorded events and how it ended)
 * or a compaction the user made. */
export type SessionEntry =
  | {
      type: "turn"
      turn: number
      question: string
      config: Record<string, unknown>
      at: string
      events: SSEEvent[]
      termination: string | null
      trajectory?: Partial<Trajectory>
    }
  | { type: "compact"; at: string; stats?: Record<string, unknown> }

/** A session's turns, to replay the transcript when resuming or switching to it. */
export async function getSession(sessionId: string, opts: ReqOpts = {}): Promise<SessionEntry[]> {
  const r = await getJson<{ turns: SessionEntry[] }>(`/sessions/${encodeURIComponent(sessionId)}`, opts)
  return r.turns
}

/** Persisted user settings: the sticky backbone (`null` until the user chose one, when the
 *  picker auto-selects a ready one) and every preference as the server has it in effect. */
export type Settings = Omit<Prefs, "default_mode" | "tool_details"> & {
  backbone?: string | null
  default_mode: string
  tool_details: string
  problem?: string // the settings file could not be used, and why
}

/** Read the persisted settings (the sticky backbone) at startup. */
export async function getSettings(opts: ReqOpts = {}): Promise<Settings> {
  return getJson<Settings>("/settings", opts)
}

/** Persist the user's backbone choice (called from /model). Returns the updated settings. */
export async function saveBackbone(backbone: string, opts: ReqOpts = {}): Promise<Settings> {
  return putJson<Settings>("/settings", { backbone }, opts)
}

/** Persist preferences (called from the Settings dialog). Returns the updated settings. */
export async function savePrefs(prefs: Partial<Prefs>, opts: ReqOpts = {}): Promise<Settings> {
  return putJson<Settings>("/settings", prefs, opts)
}

interface Note {
  name: string
  type: string
  description: string
  updated: string
}

/** What the agent remembers about the user. */
export async function listNotes(opts: ReqOpts = {}): Promise<Note[]> {
  return (await getJson<{ notes: Note[] }>("/memory", opts)).notes
}

/** Forget one note. */
export async function forgetNote(name: string, opts: ReqOpts = {}): Promise<boolean> {
  const resp = await request("DELETE", `/memory/${encodeURIComponent(name)}`, undefined, opts)
  return ((await resp.json()) as { deleted: boolean }).deleted
}

/** Context usage for a session (tokens vs window), for /status and the rail meter. */
export async function getStatus(
  sessionId?: string,
  profile?: string,
  opts: ReqOpts = {},
): Promise<StatusInfo> {
  const q = new URLSearchParams()
  if (sessionId) q.set("session_id", sessionId)
  if (profile) q.set("profile", profile)
  const qs = q.toString()
  return getJson<StatusInfo>(`/status${qs ? `?${qs}` : ""}`, opts)
}

/** Switchable backbone profiles + credential availability, for /model. */
export async function listModels(
  opts: ReqOpts = {},
): Promise<{ models: ModelInfo[]; default: string | null; default_reason: string | null }> {
  return getJson<{ models: ModelInfo[]; default: string | null; default_reason: string | null }>(
    "/models",
    opts,
  )
}

export interface CapabilityItem {
  kind: "tool" | "skill"
  name: string // wire name (load_tool) or skill name (load_skill)
  display_name: string
  family: string
  domain: string | null
  tier: string // "core" | "deferred" | "skill"
  description: string
  requires_tools?: string[] // skills only: the tools they auto-load
  tags: string[]
}

export interface DomainGroup {
  key: string
  title: string
  order: number
  tools: CapabilityItem[]
  skills: CapabilityItem[]
}

export interface DisciplineGroup {
  key: string
  title: string
  order: number
  domains: DomainGroup[]
}

export interface CapabilitiesCatalog {
  foundation: CapabilityItem[]
  disciplines: DisciplineGroup[] // financial disciplines -> subject domains -> items
  extensions: CapabilityItem[]
}

/** The browsable tools + skills catalog, grouped by domain, for the browser. */
export async function listCapabilities(opts: ReqOpts = {}): Promise<CapabilitiesCatalog> {
  return getJson<CapabilitiesCatalog>("/capabilities", opts)
}

/** Compact a session: LLM-summarize older turns, keeping the recent one verbatim. */
export async function compact(
  sessionId: string,
  instruction?: string,
  profile?: string,
  opts: ReqOpts = {},
): Promise<CompactResult> {
  return postJson<CompactResult>(
    "/compact",
    { session_id: sessionId, instruction: instruction ?? null, profile: profile ?? null },
    opts,
  )
}

// ----- channels and keys ---------------------------------------------------------------- //

import type { FieldSpec } from "./form"

export interface ChannelKind {
  name: string
  label: string
  summary: string
  fields: FieldSpec[]
  presets: Record<string, Record<string, unknown>>
}

interface SecretState {
  name: string
  configured: boolean
  source: "env" | "store" | null
}

export interface ChannelRow {
  name: string
  kind: string
  settings: Record<string, unknown>
  secrets: Record<string, SecretState>
  address: string | null
  problem?: string
}

/** A write that the server may refuse with a reason the owner can act on. */
type Written<T> = { ok: true; value: T } | { ok: false; detail: string }

async function write<T>(
  method: "POST" | "PUT" | "DELETE",
  path: string,
  body: unknown,
  opts: ReqOpts = {},
): Promise<Written<T>> {
  try {
    return { ok: true, value: (await (await request(method, path, body, opts)).json()) as T }
  } catch (e) {
    return { ok: false, detail: errorText(e) }
  }
}

export const getSync = (opts: ReqOpts = {}) => getJson<SyncStatus>("/sync", opts)

export const importSessions = (opts?: ReqOpts) =>
  write<SyncStatus & { enrolled: number }>("POST", "/sync/import", undefined, opts)

export const remoteSessions = async (opts: ReqOpts = {}) =>
  (await getJson<{ sessions: RemoteSession[] }>("/sync/remote", opts)).sessions

export const restoreSession = (id: string, opts?: ReqOpts) =>
  write<{ id: string; restored: boolean }>("POST", "/sync/restore", { id }, opts)

export const saveReport = (sessionId: string, dir?: string, opts?: ReqOpts) =>
  write<{ dir: string; files: string[] }>(
    "POST",
    `/sessions/${encodeURIComponent(sessionId)}/save`,
    dir ? { dir } : {},
    opts,
  )

export const listChannelKinds = async (opts: ReqOpts = {}) =>
  (await getJson<{ kinds: ChannelKind[] }>("/channels/kinds", opts)).kinds

export const listChannels = (opts: ReqOpts = {}) =>
  getJson<{ channels: ChannelRow[]; problems: Record<string, string> }>("/channels", opts)

export const putChannel = (
  name: string,
  body: { kind: string; settings: Record<string, unknown>; secrets: Record<string, string> },
  opts?: ReqOpts,
) => write<ChannelRow>("PUT", `/channels/${encodeURIComponent(name)}`, body, opts)

export const deleteChannel = (name: string, force = false, opts?: ReqOpts) =>
  write<{ removed: string; jobs_affected: string[] }>(
    "DELETE",
    `/channels/${encodeURIComponent(name)}${force ? "?force=true" : ""}`,
    undefined,
    opts,
  )

export interface Outcome {
  channel: string
  address: string
  status: "sent" | "failed" | "unknown"
  detail?: string
  repeated?: boolean // this send had been made already: nothing went out now
}

export const sendReport = (sessionId: string, channels: string[], once: boolean, opts?: ReqOpts) =>
  write<{ outcomes: Outcome[]; key: string }>(
    "POST",
    `/sessions/${encodeURIComponent(sessionId)}/send`,
    { channels, once },
    opts,
  )

export const testChannel = (name: string, opts?: ReqOpts) =>
  write<{ outcomes: Outcome[] }>("POST", `/channels/${encodeURIComponent(name)}/test`, undefined, opts)

interface Chat {
  id: number
  first_name: string | null
  username: string | null
}

export const findTelegramChat = (tokenEnv: string, waitS = 60, opts?: ReqOpts) =>
  write<{ chat: Chat | null }>(
    "POST",
    "/channels/telegram/chat",
    { token_env: tokenEnv, wait_s: waitS },
    opts,
  )

export interface Credential {
  name: string
  label: string
  configured: boolean
  source: "env" | "store" | null
  shadows_store?: boolean
}

export const listCredentials = (opts: ReqOpts = {}) =>
  getJson<{ credentials: Credential[]; problem: string | null }>("/credentials", opts)

export const putCredential = (name: string, value: string, opts?: ReqOpts) =>
  write<{ name: string; configured: boolean; source: string | null; shadows_store?: boolean }>(
    "PUT",
    `/credentials/${encodeURIComponent(name)}`,
    { value },
    opts,
  )

export const deleteCredential = (name: string, opts?: ReqOpts) =>
  write<{ removed: boolean }>("DELETE", `/credentials/${encodeURIComponent(name)}`, undefined, opts)

export interface JobRun {
  id: number
  status: string
  started: string
  ended: string | null
  tokens: number | null
  error: string
  delivery: { channel: string; status: string; address?: string; detail?: string }[]
}

export interface Job {
  id: string
  name: string
  prompt: string
  when: string
  next: string | null
  deliver: string[]
  enabled: boolean
  profile: string
  last_run: JobRun | null
}

interface JobsList {
  jobs: Job[]
  scheduler: boolean
  kept_running: boolean
  usage: { day_tokens: number; day_limit: number; run_limit: number }
}

export const listJobs = (opts: ReqOpts = {}) => getJson<JobsList>("/jobs", opts)

export const addJob = (
  body: {
    name: string
    prompt: string
    tz: string
    deliver: string[]
    every?: string
    time?: string
    weekday?: string
    cron?: string
    at?: string // a local "YYYY-MM-DD HH:MM" in `tz`, for a one-time run
    profile?: string
  },
  opts?: ReqOpts,
) => write<Job>("POST", "/jobs", body, opts)

export const deleteJob = (id: string, opts?: ReqOpts) =>
  write<{ deleted: boolean }>("DELETE", `/jobs/${encodeURIComponent(id)}`, undefined, opts)

export const setJobEnabled = (id: string, enabled: boolean, opts?: ReqOpts) =>
  write<{ enabled: boolean }>("POST", `/jobs/${encodeURIComponent(id)}/enabled`, { enabled }, opts)

export const runJob = (id: string, opts?: ReqOpts) =>
  write<{ run: number; status: string; reason: string }>(
    "POST",
    `/jobs/${encodeURIComponent(id)}/run`,
    undefined,
    opts,
  )

export const jobRuns = async (id: string, opts: ReqOpts = {}) =>
  (await getJson<{ runs: JobRun[] }>(`/jobs/${encodeURIComponent(id)}/runs`, opts)).runs

export interface ServiceState {
  supported: boolean
  installed?: boolean
  active?: boolean
  managed?: boolean // this backend is the service itself
  copies?: string[] // the variables an install would copy
}

export const getService = (opts: ReqOpts = {}) => getJson<ServiceState>("/service", opts)

export const installService = (opts?: ReqOpts) =>
  write<{ installed: boolean; copied: string[]; note: string }>("POST", "/service/install", undefined, opts)

export const removeService = (opts?: ReqOpts) =>
  write<{ removed: boolean }>("DELETE", "/service", undefined, opts)
