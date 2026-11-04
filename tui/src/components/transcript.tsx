// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Transcript: the streaming research view. The user's question as a header, then
 * the agent's parts (reasoning, tool, answer) rendered in arrival order as they
 * stream, in one stick-to-bottom scrollbox.
 *
 * Reasoning is kept: the header changes from "thinking…" to "reasoning" and the
 * text stays. Rendering uses <Index> (keyed by
 * position) + reactive part accessors + <markdown streaming>, so streaming
 * updates the mounted node in place instead of remounting it (no flash).
 */
import { Index, Match, Show, Switch, type JSX } from "solid-js"

import { useRenderer } from "@opentui/solid"
import {
  type CliRenderer,
  type RGBA,
  type ScrollBoxRenderable,
  SyntaxStyle,
  TextAttributes,
} from "@opentui/core"

import { formatCitations } from "../lib/citations"
import { type ToolDetails } from "../lib/settings"
import {
  type ReasoningPart,
  type StatusPart,
  type TextPart,
  type ToolPart,
  type Turn,
} from "../lib/conversation"
import { layout, reportStyles, scrollbarOptions, theme } from "../theme"
import { Spinner, SpinnerGlyph } from "./spinner"

const MARK = { running: ".", ok: "✓", error: "✗" } as const
// Label shown while a tool runs; settled rows keep the plain tool name.
const RUNNING_VERB: Record<string, string> = { search: "searching", visit: "visiting" }

/** A readable label for one arg value, never "[object Object]": strings as-is,
 *  arrays mapped element-wise, objects reduced to a salient field (else their keys). */
function argLabel(v: unknown): string {
  if (typeof v === "string") return v
  if (typeof v === "number" || typeof v === "boolean") return String(v)
  if (Array.isArray(v)) return v.map(argLabel).filter(Boolean).join(", ")
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>
    for (const k of ["step", "query", "url", "name", "title", "symbol", "ticker", "goal"]) {
      if (typeof o[k] === "string") return o[k] as string
    }
    return Object.keys(o).join(", ")
  }
  return ""
}

/** A tool's arguments on one line: brief is the values alone (queries, urls, goal), cut at 56
 *  characters; full is every argument by name. */
function formatArgs(args: Record<string, unknown>, details: ToolDetails): string {
  if (details === "full") {
    return Object.entries(args)
      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
      .join(", ")
  }
  const s = Object.values(args).map(argLabel).filter(Boolean).join(" / ")
  return s.length > 56 ? s.slice(0, 55) + "…" : s
}

/** update_plan is a UI tool: a terse summary here; the live checklist is the rail Plan panel. */
function planSummary(args: Record<string, unknown>): string {
  const steps = Array.isArray(args.steps) ? (args.steps as { status?: string }[]) : []
  const done = steps.filter((s) => s?.status === "completed").length
  return `${done}/${steps.length} steps`
}

/** Content with a one-column bar on its left. The bar is a filled box, not a border: a
 *  border glyph of a child that is partly scrolled out of its scrollbox is drawn outside
 *  the clip (over the frame's title row), while a background fill is clipped correctly. */
function Bar(props: { color: RGBA; marginLeft?: number; children: JSX.Element }) {
  return (
    <box flexDirection="row" flexShrink={0} marginLeft={props.marginLeft}>
      <box width={1} flexShrink={0} backgroundColor={props.color} />
      <box flexDirection="column" flexShrink={1} flexGrow={1} minWidth={0} paddingLeft={1}>
        {props.children}
      </box>
    </box>
  )
}

function ToolRow(props: { part: () => ToolPart; details: ToolDetails }) {
  const p = () => props.part()
  const color = () =>
    p().status === "ok" ? theme.success : p().status === "error" ? theme.error : theme.accent
  const elapsed = () => (p().elapsedS != null ? ` (${p().elapsedS}s)` : "")
  const detail = () => (p().status === "running" ? p().detail : undefined)
  const label = () => (p().status === "running" && RUNNING_VERB[p().name]) || p().name
  return (
    <box flexDirection="column" flexShrink={0}>
      <box flexDirection="row" flexShrink={0}>
        {/* spinner while running; ✓/✗ when settled */}
        <Show when={p().status === "running"} fallback={<text fg={color()}>{MARK[p().status]} </text>}>
          <SpinnerGlyph color={theme.accent} />
        </Show>
        <text fg={theme.textMuted}>{label()} </text>
        <text fg={theme.textMuted}>
          {p().name === "update_plan" ? planSummary(p().args) : formatArgs(p().args, props.details)}
          {elapsed()}
        </text>
      </box>
      <Show when={detail()}>
        <text fg={theme.textMuted}>{"   - " + detail()}</text>
      </Show>
      <Show when={p().result}>
        {/* the fetched/computed figures behind the call (data_/compute_ only; truncated
            server-side), under the dim `border` rule, quieter than the reasoning block. */}
        <Bar color={theme.border} marginLeft={2}>
          <text fg={theme.textMuted}>{p().result!.trimEnd()}</text>
        </Bar>
      </Show>
    </box>
  )
}

function ReasoningBlock(props: { part: () => ReasoningPart; show: boolean }) {
  const p = () => props.part()
  // hidden reasoning leaves a one-line "thinking…" while it streams, and nothing once it ends
  if (!props.show) return <text fg={theme.accent}>thinking…</text>
  return (
    <Bar color={theme.reasoningBar}>
      <text fg={p().done ? theme.textMuted : theme.accent}>{p().done ? "reasoning" : "thinking…"}</text>
      <text fg={theme.textMuted} attributes={TextAttributes.ITALIC}>
        {p().text.trimEnd()}
      </text>
    </Bar>
  )
}

/** One turn: question header, parts timeline, phase spinner or error. */
const styles = new WeakMap<CliRenderer, SyntaxStyle>()
/** The report's markdown style: one native object per renderer, which owns it. */
function reportStyle(renderer: CliRenderer): SyntaxStyle {
  let style = styles.get(renderer)
  if (!style) {
    style = SyntaxStyle.fromStyles(reportStyles, renderer.nativeScene)
    styles.set(renderer, style)
  }
  return style
}

function TurnView(props: {
  turn: () => Turn
  first: boolean
  showReasoning: boolean
  toolDetails: ToolDetails
}) {
  const renderer = useRenderer()
  const t = () => props.turn()
  return (
    <box flexDirection="column" flexShrink={0}>
      <Show when={!props.first}>
        <box height={1} flexShrink={0} />
      </Show>
      <box flexDirection="row" flexShrink={0}>
        <text fg={theme.primary}>› </text>
        <text fg={theme.text} attributes={TextAttributes.BOLD}>
          {t().question}
        </text>
      </box>
      <box height={1} flexShrink={0} />
      <Index each={t().parts}>
        {(part) => (
          <Show when={props.showReasoning || part().kind !== "reasoning" || !(part() as ReasoningPart).done}>
            <box flexShrink={0} paddingBottom={1}>
              <Switch>
                <Match when={part().kind === "reasoning"}>
                  <ReasoningBlock part={part as () => ReasoningPart} show={props.showReasoning} />
                </Match>
                <Match when={part().kind === "tool"}>
                  <ToolRow part={part as () => ToolPart} details={props.toolDetails} />
                </Match>
                <Match when={part().kind === "status"}>
                  <Show
                    when={(part() as StatusPart).running}
                    fallback={<text fg={theme.textMuted}>{(part() as StatusPart).text}</text>}
                  >
                    <Spinner label={(part() as StatusPart).text} color={theme.info} />
                  </Show>
                </Match>
                <Match when={part().kind === "text"}>
                  <box flexShrink={0}>
                    {/* top-level block mode: each heading/paragraph is its own block,
                      so settled blocks aren't re-rendered as new tokens stream. streaming
                      flips off once the turn is answered so trailing tokens finalize. */}
                    <markdown
                      content={formatCitations((part() as TextPart).text)}
                      syntaxStyle={reportStyle(renderer)}
                      internalBlockMode="top-level"
                      tableOptions={{ style: "grid" }}
                      streaming={t().status === "running"}
                    />
                  </box>
                </Match>
              </Switch>
            </box>
          </Show>
        )}
      </Index>
      {/* Live server-side phase (e.g. grounding the draft against sources) shown
          inline in the transcript. `phase` is cleared the instant answer tokens stream. */}
      <Show when={t().status === "running" && t().phase}>
        <box flexShrink={0} paddingBottom={1}>
          {/* a server phase (e.g. grounding), colored distinctly from tool rows */}
          <Spinner label={`${t().phase}…`} color={theme.info} />
        </box>
      </Show>
      <Show when={t().status === "cancelled"}>
        <text fg={theme.textMuted}>cancelled</text>
      </Show>
      <Show when={t().status === "error" && t().error}>
        <text fg={theme.error}>error: {t().error}</text>
      </Show>
    </box>
  )
}

export function Transcript(props: {
  turns: Turn[]
  scrollRef?: (r: ScrollBoxRenderable) => void
  showReasoning: boolean
  toolDetails: ToolDetails
}) {
  // The frame clips what scrolls under it: a bordered block that is partly scrolled out would
  // otherwise draw its edge over the title and the frame.
  return (
    <box
      flexGrow={1}
      minWidth={0}
      minHeight={0}
      overflow="hidden"
      border
      borderStyle={layout.border}
      title="Trading-R1"
      borderColor={theme.border}
      titleColor={theme.primary}
    >
      <scrollbox
        ref={props.scrollRef}
        flexGrow={1}
        minHeight={0}
        stickyScroll
        stickyStart="bottom"
        paddingTop={layout.pad.docked.y}
        paddingLeft={layout.pad.docked.x}
        paddingRight={layout.pad.docked.x}
        verticalScrollbarOptions={scrollbarOptions}
      >
        <Index each={props.turns}>
          {(turn, i) => (
            <TurnView
              turn={turn}
              first={i === 0}
              showReasoning={props.showReasoning}
              toolDetails={props.toolDetails}
            />
          )}
        </Index>
      </scrollbox>
    </box>
  )
}
