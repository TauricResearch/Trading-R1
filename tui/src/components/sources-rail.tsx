// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * The right rail's evidence panes, stacked under the plan:
 * - DataRail: each data result's evidence record (`#3 market_prices AAPL`, then its as-of date
 *   and source); shown once the turn has one.
 * - SourcesRail: while the agent works, the sources as they are read (pages `visit` read and
 *   the articles the news and digest tools returned); once the answer is in, only those it
 *   cites, under the numbers the answer and its References use. The rest stay in the session
 *   log. Each entry: its title, which links to the source, and its lead. Below: the context meter and run meta.
 * Both stream in live.
 */
import { For, Show } from "solid-js"

import { type StatusInfo } from "../lib/api"
import { contextMeter } from "../lib/context"
import { type RecordItem, type SourceItem, type Turn, turnMeta } from "../lib/conversation"
import { layout, md, scrollbarOptions, theme } from "../theme"

/** `market_prices AAPL (income)`: the tool and what it is about. */
export function recordTitle(r: RecordItem): string {
  const view = r.view ? ` (${r.view})` : ""
  return `${r.tool.replaceAll("_", " ")}${r.subject ? ` ${r.subject}` : ""}${view}`
}

/** `as of 2026-10-16, SEC filings`: whichever of the two the record has. */
export function recordDetail(r: RecordItem): string {
  return [r.asOf ? `as of ${r.asOf}` : null, r.source].filter(Boolean).join(", ")
}

/** The conversation's sources, each once, under the number it has for the whole conversation. */
export function conversationSources(turns: { sources: SourceItem[] }[]): SourceItem[] {
  const byUrl = new Map<string, SourceItem>()
  for (const turn of turns) for (const s of turn.sources) if (!byUrl.has(s.url)) byUrl.set(s.url, s)
  return [...byUrl.values()].sort((a, b) => a.index - b.index)
}

function SourceEntry(props: { source: SourceItem }) {
  const s = () => props.source
  return (
    <box flexDirection="column" flexShrink={0} paddingBottom={1} paddingRight={2}>
      <text wrapMode="word">
        <span style={{ fg: theme.accent }}>[{s().index}] </span>
        <a href={s().url} style={{ fg: md.link, underline: true }}>
          {s().title}
        </a>
      </text>
      <Show when={s().headline && s().headline !== s().title}>
        <text fg={theme.textMuted} wrapMode="word">
          {s().headline}
        </text>
      </Show>
    </box>
  )
}

function Pane(props: { title: string; grow: number; children: unknown }) {
  return (
    <box
      flexGrow={props.grow}
      flexBasis={0}
      minHeight={0}
      overflow="hidden"
      border
      borderStyle={layout.border}
      title={props.title}
      borderColor={theme.border}
      titleColor={theme.accent}
      backgroundColor={theme.backgroundPanel}
      flexDirection="column"
      paddingTop={layout.pad.docked.y}
      paddingLeft={layout.pad.docked.x}
      paddingRight={layout.pad.docked.x}
    >
      {props.children as never}
    </box>
  )
}

export function DataRail(props: { turn: Turn }) {
  const records = () => props.turn.records
  return (
    <Show when={records().length > 0}>
      <Pane title={`Data (${records().length})`} grow={1}>
        <scrollbox
          flexGrow={1}
          minHeight={0}
          stickyScroll
          stickyStart="top"
          verticalScrollbarOptions={scrollbarOptions}
        >
          <For each={records()}>
            {(r) => (
              <box flexDirection="column" flexShrink={0} paddingBottom={1} paddingRight={2}>
                <text wrapMode="word">
                  <span style={{ fg: theme.accent }}>#{r.id.slice(1)}</span> {recordTitle(r)}
                </text>
                <Show when={recordDetail(r)}>
                  <text fg={theme.textMuted} wrapMode="word">
                    {recordDetail(r)}
                  </text>
                </Show>
              </box>
            )}
          </For>
        </scrollbox>
      </Pane>
    </Show>
  )
}

export function SourcesRail(props: { turn: Turn; sources: SourceItem[]; usage: StatusInfo | null }) {
  const meta = () => turnMeta(props.turn)
  const meter = () => contextMeter(props.usage)
  return (
    <Pane title={`Sources (${props.sources.length})`} grow={2}>
      <scrollbox
        flexGrow={1}
        minHeight={0}
        stickyScroll
        stickyStart="top"
        verticalScrollbarOptions={scrollbarOptions}
      >
        <Show
          when={props.sources.length > 0}
          fallback={
            <text fg={theme.textMuted} wrapMode="word">
              no sources yet
            </text>
          }
        >
          <For each={props.sources}>{(s) => <SourceEntry source={s} />}</For>
        </Show>
      </scrollbox>
      <box flexDirection="column" flexShrink={0} borderColor={theme.border} border={["top"]}>
        <text fg={meter().warn ? theme.warning : theme.textMuted} wrapMode="word">
          {meter().usage}
        </text>
        <text fg={theme.textMuted} wrapMode="word">
          {meter().compaction}
        </text>
        <text fg={theme.textMuted} wrapMode="word">
          {meta().rounds} rounds, {meta().tools} tools
          {meta().elapsedS != null ? `, ${Math.round(meta().elapsedS!)}s` : ""}
        </text>
      </box>
    </Pane>
  )
}
