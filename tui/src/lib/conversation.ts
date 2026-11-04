// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Conversation model: a pure reducer that folds the SSE event stream into a
 * `Turn` with an ordered `parts[]` timeline (reasoning, tool, text), so the
 * transcript follows the agent's actual flow (think, call, think, answer).
 * Framework-agnostic and immutable, so it tests without a renderer.
 *
 * The app batches high-frequency reasoning/token deltas before
 * applying, so cloning per event is cheap.
 */
import type { Citation, SessionEntry, SSEEvent, Trajectory } from "./api"
import { tokensK } from "./format"

export interface ReasoningPart {
  kind: "reasoning"
  text: string
  done: boolean // closed once non-reasoning output begins (collapsible in the UI)
}
export interface ToolPart {
  kind: "tool"
  name: string
  args: Record<string, unknown>
  round: number
  status: "running" | "ok" | "error"
  elapsedS?: number
  callId?: string
  detail?: string // live sub-status while running (e.g. visit fetching/reading)
  result?: string // truncated data_/compute_ output, for auditing the figures (trace)
}
export interface TextPart {
  kind: "text"
  text: string
}
/** A line of status in the timeline: the context being compacted, running or done. */
export interface StatusPart {
  kind: "status"
  text: string
  running: boolean
}
type Part = ReasoningPart | ToolPart | TextPart | StatusPart

/** A source read by `visit`, surfaced live (numbered, with a reader headline). */
export interface SourceItem {
  index: number
  url: string
  title: string
  headline: string
}

/** A data result's evidence record (`e3`): the tool, what it is about, the date its data
 * runs to and the upstream source: the rail's Data section. */
export interface RecordItem {
  id: string
  tool: string
  subject: string | null
  view?: string | null
  asOf?: string | null
  source?: string | null
}

/** A step in the live research plan (from the `update_plan` tool). */
export interface PlanStep {
  step: string
  status: "pending" | "in_progress" | "completed"
}

type TurnStatus = "running" | "answered" | "error" | "cancelled"

export interface Turn {
  question: string
  status: TurnStatus
  parts: Part[]
  citations: Citation[]
  sources: SourceItem[] // live sources from `visit` (numbered, with headlines)
  records: RecordItem[] // data results' evidence records, in call order
  plan: PlanStep[] // live research plan/checklist from `update_plan`
  rounds: number
  toolCount: number
  elapsedS?: number
  termination?: string
  error?: string
  phase?: string // a coarse server-side processing phase (e.g. "grounding") for live status
  trajectory?: Trajectory // the raw final trajectory (kept for /export)
}

/** Terminations that carry an answer (the service's ANSWERED). */
const ANSWERED = new Set(["answer", "budget_report"])

export function newTurn(question: string, status: TurnStatus = "running"): Turn {
  return {
    question,
    status,
    parts: [],
    citations: [],
    sources: [],
    records: [],
    plan: [],
    rounds: 0,
    toolCount: 0,
  }
}

/** Close a trailing open reasoning block when non-reasoning output starts. */
function closeReasoning(parts: Part[]): Part[] {
  const last = parts[parts.length - 1]
  if (last && last.kind === "reasoning" && !last.done) {
    const out = parts.slice()
    out[out.length - 1] = { ...last, done: true }
    return out
  }
  return parts
}

/** The parts with the turn's last text settled: what the model wrote after its last call
 * becomes this text, written once. */
function placeAnswer(parts: Part[], content: string): Part[] {
  let last = -1
  for (let i = parts.length - 1; i >= 0; i--) {
    if (parts[i].kind === "tool") {
      last = i
      break
    }
  }
  const after = parts.slice(last + 1).filter((p) => p.kind !== "text")
  return [...parts.slice(0, last + 1), ...after, { kind: "text", text: content }]
}

export function applyTurnEvent(turn: Turn, raw: SSEEvent): Turn {
  const ev = raw as SSEEvent
  switch (ev.event) {
    case "run_start":
      return { ...turn, status: "running" }

    case "round_start": {
      const r = ev.data.round
      return { ...turn, status: "running", rounds: Math.max(turn.rounds, r) }
    }

    case "reasoning": {
      const text = ev.data.text
      const parts = turn.parts.slice()
      const last = parts[parts.length - 1]
      if (last && last.kind === "reasoning" && !last.done) {
        parts[parts.length - 1] = { ...last, text: last.text + text }
      } else {
        parts.push({ kind: "reasoning", text, done: false })
      }
      return { ...turn, status: "running", parts }
    }

    case "tool_call": {
      const d = ev.data
      const parts = closeReasoning(turn.parts).concat({
        kind: "tool",
        name: d.name,
        args: d.args ?? {},
        round: d.round ?? turn.rounds,
        status: "running",
        callId: d.call_id,
      })
      return { ...turn, status: "running", parts, toolCount: turn.toolCount + 1 }
    }

    case "tool_progress": {
      const d = ev.data
      const parts = turn.parts.slice()
      for (let i = parts.length - 1; i >= 0; i--) {
        const p = parts[i]
        if (p.kind === "tool" && p.status === "running" && (!d.call_id || p.callId === d.call_id)) {
          parts[i] = { ...p, detail: d.detail }
          break
        }
      }
      return { ...turn, parts }
    }

    case "tool_result": {
      const d = ev.data
      const parts = turn.parts.slice()
      for (let i = parts.length - 1; i >= 0; i--) {
        const p = parts[i]
        if (p.kind !== "tool" || p.status !== "running") continue
        // match by call_id when present (correct for repeated tools), else by name
        const hit = d.call_id ? p.callId === d.call_id : p.name === d.name
        if (hit) {
          parts[i] = {
            ...p,
            status: d.ok ? "ok" : "error",
            elapsedS: d.elapsed_s,
            result: d.result ?? undefined,
          }
          break
        }
      }
      return { ...turn, parts }
    }

    case "token": {
      const text = ev.data.text
      // Models emit stray whitespace-only content (e.g. "\n\n") at round
      // boundaries; turning each into a text part makes empty blocks and splits
      // the reasoning stream (isolating tiny fragments). Skip it unless it's
      // extending an answer already in progress.
      const prev = turn.parts[turn.parts.length - 1]
      if (text.trim() === "" && !(prev && prev.kind === "text")) return turn
      let parts = closeReasoning(turn.parts)
      const last = parts[parts.length - 1]
      if (last && last.kind === "text") {
        parts = parts.slice()
        parts[parts.length - 1] = { ...last, text: last.text + text }
      } else {
        parts = parts.concat({ kind: "text", text: text.trimStart() })
      }
      return { ...turn, status: "running", parts, phase: undefined }
    }

    case "compaction": {
      const d = ev.data
      const before = tokensK(d.tokens_before)
      const text =
        d.stage === "start"
          ? `Compacting context: ${before} tokens…`
          : d.stage === "failed"
            ? "Context compaction failed, continuing without it"
            : d.how === "trim"
              ? `Trimmed old tool results: ${before} to ${tokensK(d.tokens_after ?? 0)} tokens`
              : `Compacted context: ${before} to ${tokensK(d.tokens_after ?? 0)} tokens`
      const part: StatusPart = { kind: "status", text, running: d.stage === "start" }
      const parts = turn.parts.slice()
      const last = parts[parts.length - 1]
      // the end (or failure) of a compaction replaces its "compacting…" line
      if (d.stage !== "start" && last?.kind === "status" && last.running) parts[parts.length - 1] = part
      else parts.push(part)
      return { ...turn, status: "running", parts }
    }

    case "phase":
      return { ...turn, status: "running", phase: ev.data.label }

    case "last_message": {
      const content = ev.data.content
      return { ...turn, parts: placeAnswer(closeReasoning(turn.parts), content), phase: undefined }
    }

    case "source": {
      // a page read by visit: append live (dedupe/replace by citation index).
      const s: SourceItem = ev.data
      const sources = turn.sources.slice()
      const at = sources.findIndex((x) => x.index === s.index)
      if (at >= 0) sources[at] = s
      else sources.push(s)
      sources.sort((a, b) => a.index - b.index)
      return { ...turn, sources }
    }

    case "record": {
      // a data result's evidence record, appended in call order
      const d = ev.data
      const record: RecordItem = {
        id: d.id,
        tool: d.tool,
        subject: d.subject,
        view: d.view,
        asOf: d.as_of,
        source: d.source,
      }
      return { ...turn, records: turn.records.concat(record) }
    }

    case "plan":
      // the model's live research plan (full list replaces the prior one)
      return { ...turn, plan: ev.data.items }

    case "error":
      return { ...turn, status: "error", error: ev.data.error }

    case "done": {
      const t = ev.data.trajectory
      if (!t) return { ...turn, status: turn.status === "error" ? "error" : "answered" }
      let parts = closeReasoning(turn.parts)
      if (t.text) parts = placeAnswer(parts, t.text)
      // a run that ended without an answer (an error, a full context) shows as one
      const answered = ANSWERED.has(t.termination)
      return {
        ...turn,
        status: answered ? "answered" : "error",
        error: answered ? turn.error : (turn.error ?? (t.error || t.termination)),
        parts,
        citations: t.citations ?? [],
        rounds: Math.max(turn.rounds, t.rounds ?? 0),
        elapsedS: t.elapsed_s ?? turn.elapsedS,
        termination: t.termination,
        trajectory: t,
      }
    }

    default:
      return turn
  }
}

/** Rebuild a resumed session's transcript by replaying each turn's recorded events through
 * the same fold the live view uses, so a turn looks as it did when it ran: reasoning, tool calls
 * and results, sources, plan, answer. How it ended closes it: the trajectory for a finished
 * turn, "cancelled" for one stopped by the user, "interrupted" for one with no recorded end. A
 * user compaction shows where it happened; the turns before it stay. */
export function turnsFromSession(entries: SessionEntry[]): Turn[] {
  return entries.map((e) => {
    if (e.type === "compact") {
      const text = "Conversation compacted here: the model continues from a summary of the turns above."
      return { ...newTurn("/compact", "answered"), parts: [{ kind: "text", text }] }
    }
    const turn = e.events.reduce(applyTurnEvent, newTurn(e.question))
    if (e.termination === null) return { ...turn, status: "error", error: turn.error ?? "interrupted" }
    if (e.termination === "cancelled") return { ...turn, status: "cancelled" }
    const trajectory = { ...e.trajectory, question: e.question, termination: e.termination } as Trajectory
    return applyTurnEvent(turn, {
      event: "done",
      data: { termination: e.termination, trajectory, session_id: null },
    })
  })
}

interface TurnMeta {
  rounds: number
  tools: number
  sources: number
  elapsedS?: number
}

export function turnMeta(turn: Turn): TurnMeta {
  return {
    rounds: turn.rounds,
    tools: turn.toolCount,
    sources: turn.sources.length || turn.citations.length,
    elapsedS: turn.elapsedS,
  }
}
