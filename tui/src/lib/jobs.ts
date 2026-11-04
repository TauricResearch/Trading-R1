// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Scheduled reports: the words and the forms for them. Pure: the screens in setup/jobs.tsx call the
 * backend; this decides what a job, a run and the day's usage are called, and which fields each
 * way of repeating asks for.
 */
import type { Job, JobRun, addJob } from "./api"
import type { FieldSpec } from "./form"
import { tokens, utcMinute } from "./format"

const field = (f: Partial<FieldSpec> & Pick<FieldSpec, "name" | "label" | "type">): FieldSpec => ({
  required: true,
  default: null,
  choices: [],
  minimum: null,
  maximum: null,
  help: "",
  example: "",
  ...f,
})

export const REPEATS = [
  { value: "weekdays", name: "Every weekday", description: "Monday to Friday at a time you choose" },
  { value: "day", name: "Every day", description: "including weekends" },
  { value: "week", name: "Every week", description: "on one day of the week" },
  { value: "once", name: "Once", description: "one date and time" },
  { value: "cron", name: "Custom schedule", description: "a cron expression, e.g. 0 8 * * 1-5" },
] as const

export type Repeat = (typeof REPEATS)[number]["value"]

/** The fields a new job asks for; what is asked about "when" depends on how it repeats. */
export function jobFields(repeat: Repeat, channels: string[], timeZone: string): FieldSpec[] {
  const when: FieldSpec[] =
    repeat === "cron"
      ? [
          field({
            name: "cron",
            label: "Schedule",
            type: "text",
            example: "0 8 * * 1-5",
            help: "Five fields: minute hour day month weekday. At least 15 minutes apart.",
          }),
        ]
      : repeat === "once"
        ? [
            field({ name: "date", label: "Date", type: "text", example: "2026-11-02", help: "YYYY-MM-DD." }),
            field({
              name: "time",
              label: "Time",
              type: "text",
              default: "08:00",
              example: "08:00",
              help: "24-hour clock, HH:MM.",
            }),
          ]
        : [
            ...(repeat === "week"
              ? [
                  field({
                    name: "weekday",
                    label: "Day",
                    type: "choice",
                    choices: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                    default: "mon",
                  }),
                ]
              : []),
            field({
              name: "time",
              label: "Time",
              type: "text",
              default: "08:00",
              example: "08:00",
              help: "24-hour clock, HH:MM.",
            }),
          ]
  return [
    field({ name: "name", label: "Name", type: "text", example: "Morning brief" }),
    field({
      name: "prompt",
      label: "Question",
      type: "text",
      help: "Asked again each time, on a fresh session. Name the securities and what you want to know.",
      example: "What moved the market since yesterday's close, and what matters for my watchlist?",
    }),
    ...when,
    field({
      name: "tz",
      label: "Time zone",
      type: "text",
      default: timeZone,
      example: "America/New_York",
      help: "IANA name. The time above uses this zone.",
    }),
    field({
      name: "deliver",
      label: "Send to",
      type: "list",
      default: channels.length === 1 ? channels[0] : null,
      help: `Your channels: ${channels.join(", ")}.`,
    }),
  ]
}

/** The request that makes a job of a filled form: the way it repeats decides which of "when"
 *  is sent. A one-time run sends its local date and time with the zone; the server converts. */
export function jobBody(
  repeat: Repeat,
  s: Record<string, string | string[]>,
  profile: string | undefined,
): Parameters<typeof addJob>[0] {
  const when =
    repeat === "cron"
      ? { cron: String(s.cron) }
      : repeat === "once"
        ? { at: `${s.date} ${s.time}` }
        : { every: repeat, time: String(s.time), weekday: s.weekday ? String(s.weekday) : undefined }
  return {
    name: String(s.name),
    prompt: String(s.prompt),
    tz: String(s.tz),
    deliver: s.deliver as string[],
    profile,
    ...when,
  }
}

export function usageLine(u: { day_tokens: number; day_limit: number; run_limit: number }): string {
  return `Last 24 h: ${tokens(u.day_tokens)} of ${tokens(u.day_limit)} tokens, one run stops at ${tokens(u.run_limit)}`
}

export function runLine(run: JobRun): string {
  const bits = [run.status, utcMinute(run.started)]
  if (run.tokens != null) bits.push(`${tokens(run.tokens)} tokens`)
  const unsent = run.delivery.filter((d) => d.status !== "sent")
  if (run.delivery.length && !unsent.length) bits.push("delivered")
  for (const d of unsent) bits.push(`${d.channel}: ${d.status}${d.detail ? ` (${d.detail})` : ""}`)
  if (run.error) bits.push(run.error)
  return bits.join(", ")
}

export function jobRow(j: Job): { name: string; description: string } {
  const state = j.enabled ? "" : " [off]"
  const last = j.last_run ? `last: ${runLine(j.last_run)}` : "not run yet"
  const to = j.deliver.length ? `to ${j.deliver.join(", ")}` : "to no channels"
  return { name: `${j.name}${state}`, description: `${j.when} ${to} / ${last}` }
}
