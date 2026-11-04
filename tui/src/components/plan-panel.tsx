// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * PlanPanel: the live research plan (from the `update_plan` tool) as a checklist in
 * the right rail: `[✓]` done, `[•]` in-progress (active, accented), `[ ]` pending.
 * Shown only while there's a plan with unfinished work.
 */
import { For, Show } from "solid-js"

import { type PlanStep } from "../lib/conversation"
import { layout, theme } from "../theme"

const MARK = { completed: "✓", in_progress: "•", pending: " " } as const

function StepRow(props: { item: PlanStep }) {
  const active = () => props.item.status === "in_progress"
  const color = () => (active() ? theme.warning : theme.textMuted)
  // The step stays on one row: it grows into the rail's remaining width and the
  // renderer truncates (lib-native, with an ellipsis) instead of wrapping; the mark
  // is pinned so it never gets squeezed.
  return (
    <box flexDirection="row" flexShrink={0}>
      <text fg={color()} flexShrink={0}>
        [{MARK[props.item.status] ?? " "}]{" "}
      </text>
      <text fg={color()} flexGrow={1} flexShrink={1} wrapMode="none" truncate>
        {props.item.step}
      </text>
    </box>
  )
}

export function PlanPanel(props: { plan: PlanStep[] }) {
  // hide once nothing remains (all completed) or there's no plan at all
  const show = () => props.plan.length > 0 && props.plan.some((s) => s.status !== "completed")
  return (
    <Show when={show()}>
      <box
        flexShrink={0}
        border
        borderStyle={layout.border}
        title="Plan"
        borderColor={theme.border}
        titleColor={theme.accent}
        backgroundColor={theme.backgroundPanel}
        flexDirection="column"
        paddingTop={layout.pad.docked.y}
        paddingLeft={layout.pad.docked.x}
        paddingRight={layout.pad.docked.x}
      >
        <For each={props.plan}>{(item) => <StepRow item={item} />}</For>
      </box>
    </Show>
  )
}
