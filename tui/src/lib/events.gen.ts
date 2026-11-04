// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

// Generated from contract/events.py; do not edit.
// The service validates every frame against these models before it ships.

export interface RunStart {
  version: string
  question: string
  mode: string
}

export interface RoundStart {
  round: number
}

export interface Reasoning {
  text: string
}

export interface Token {
  text: string
}

export interface ToolCall {
  name: string
  call_id: string
  args: Record<string, unknown>
  round: number
}

export interface ToolProgress {
  name: string | null
  call_id: string | null
  detail: string
}

export interface ToolResult {
  name: string
  call_id: string
  ok: boolean
  elapsed_s: number
  round: number
  result: string | null
}

export interface Source {
  name: string | null
  call_id: string | null
  index: number
  url: string
  title: string
  headline: string
}

export interface DataRecord {
  call_id: string
  id: string
  tool: string
  subject: string | null
  view: string | null
  as_of: string | null
  source: string | null
}

export interface PlanItem {
  step: string
  status: "pending" | "in_progress" | "completed"
}

export interface Plan {
  name: string | null
  call_id: string | null
  items: PlanItem[]
}

export interface Phase {
  label: string
}

export interface Usage {
  tokens_used: number
  context_window: number
  used_pct: number
  compact_at: number | null
}

export interface Compaction {
  stage: "start" | "end" | "failed"
  how: "trim" | "summary"
  tokens_before: number
  tokens_after: number | null
}

export interface AskOption {
  label: string
  description: string
}

export interface AskQuestion {
  id: string
  question: string
  header: string
  options: AskOption[]
}

export interface AskUser {
  request_id: string
  questions: AskQuestion[]
  timeout_s: number
}

export interface LastMessage {
  content: string
}

export interface RunError {
  error: string
}

export interface Citation {
  index: number
  url: string
  title: string
  record: string | null
}

export interface ToolLogEntry {
  name: string
  ok: boolean
}

export interface Trajectory {
  question: string
  text: string
  termination: string
  error: string | null
  rounds: number
  tool_log: ToolLogEntry[]
  citations: Citation[]
  citation_stats: Record<string, unknown>
  elapsed_s: number | null
  session_id: string | null
}

export interface Done {
  termination: string | null
  trajectory: Trajectory | null
  session_id: string | null
}

export type SSEEvent =
  | { event: "run_start"; data: RunStart }
  | { event: "round_start"; data: RoundStart }
  | { event: "reasoning"; data: Reasoning }
  | { event: "token"; data: Token }
  | { event: "tool_call"; data: ToolCall }
  | { event: "tool_progress"; data: ToolProgress }
  | { event: "tool_result"; data: ToolResult }
  | { event: "source"; data: Source }
  | { event: "record"; data: DataRecord }
  | { event: "plan"; data: Plan }
  | { event: "phase"; data: Phase }
  | { event: "usage"; data: Usage }
  | { event: "compaction"; data: Compaction }
  | { event: "ask_user"; data: AskUser }
  | { event: "last_message"; data: LastMessage }
  | { event: "error"; data: RunError }
  | { event: "done"; data: Done }
