// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Scheduled reports: the list, a job's actions and details, and the form for a new one. */
import {
  type Job,
  addJob,
  deleteJob,
  jobRuns,
  listChannels,
  listJobs,
  runJob,
  setJobEnabled,
} from "../lib/api"
import { utcMinute } from "../lib/format"
import { newForm, serverErrors } from "../lib/form"
import { REPEATS, type Repeat, jobBody, jobFields, jobRow, runLine, usageLine } from "../lib/jobs"
import { FormDialog } from "../ui/form-dialog"
import type { ListOption } from "../ui/list-dialog"
import { ADD, BACK, type Flow } from "./flow"

/** `channels` opens the send channels, where a report with nowhere to go is sent to add one. */
export function createJobs(
  { host, dialog, guarded, load, once, choose, say }: Flow,
  channels: () => Promise<void>,
) {
  function jobs() {
    return load("Scheduled reports", listJobs(), showJobs)
  }

  function showJobs(data: Awaited<ReturnType<typeof listJobs>>) {
    const options: ListOption[] = [
      {
        name: !data.scheduler
          ? "No scheduler here: reports will not fire"
          : data.kept_running || !data.jobs.length
            ? usageLine(data.usage)
            : "Reports run only while this backend runs: see Background service…",
        description: "",
        value: "",
        disabled: true,
      },
      ...data.jobs.map((j) => ({ ...jobRow(j), value: j.id })),
      {
        name: "New scheduled report…",
        description: "a question asked on a schedule and sent to your channels",
        value: ADD,
      },
    ]
    choose(
      "Scheduled reports",
      options,
      (v) => (v === ADD ? newJob() : jobActions(data.jobs.find((j) => j.id === v)!)),
      () => dialog.close(),
      "select",
      "close",
      1, // past the usage line
    )
  }

  function jobActions(j: Job) {
    choose(
      j.name,
      [
        { name: "Details", description: "the question, schedule and recent runs", value: "details" },
        { name: "Run now", description: `sends to ${j.deliver.join(", ") || "no channels"}`, value: "run" },
        j.enabled
          ? { name: "Turn off", description: "keep it, but do not run it", value: "off" }
          : { name: "Turn on", description: "run it on its schedule again", value: "on" },
        { name: "Remove", description: "delete it; what it spent stays counted", value: "remove" },
      ],
      (v) => {
        if (v === "details") return void jobDetails(j)
        if (v === "run") return void once(() => startJob(j))
        if (v === "remove") return confirmRemoveJob(j)
        void once(async () => {
          const r = await setJobEnabled(j.id, v === "on")
          if (!r.ok) return say(j.name, [r.detail], () => jobActions(j))
          host.notify(`${j.name} turned ${v}`)
          await jobs()
        })
      },
      jobs,
    )
  }

  async function jobDetails(j: Job) {
    await guarded(
      jobRuns(j.id).catch(() => null),
      (runs) => {
        say(
          j.name,
          [
            `Question: ${j.prompt}`,
            `Schedule: ${j.when}${j.next ? `, next ${utcMinute(j.next)}` : ""}`,
            `Sends to: ${j.deliver.join(", ") || "no channels"} / model ${j.profile}`,
            "",
            ...(runs === null
              ? ["Recent runs are unavailable."]
              : runs.length
                ? runs.slice(0, 5).map(runLine)
                : ["Not run yet."]),
          ],
          () => jobActions(j),
        )
      },
    )
  }

  async function startJob(j: Job) {
    const r = await runJob(j.id)
    if (!r.ok) host.notify(`${j.name}: ${r.detail}`)
    else if (r.value.status !== "running") {
      host.notify(`${j.name} did not start${r.value.reason ? `: ${r.value.reason}` : ""}`)
    } else host.notify(`${j.name} is running; the report is sent when it is done`)
    await jobs()
  }

  function confirmRemoveJob(j: Job) {
    choose(
      `Remove ${j.name}?`,
      [
        { name: "Keep it", description: "", value: BACK },
        { name: "Remove it", description: "it will not run again", value: "remove" },
      ],
      (v) => {
        if (v !== "remove") return jobActions(j)
        void once(async () => {
          const r = await deleteJob(j.id)
          if (!r.ok) return say(j.name, [r.detail], () => jobActions(j))
          host.notify(`${j.name} removed`)
          await jobs()
        })
      },
      () => jobActions(j),
    )
  }

  function newJob() {
    return load(
      "Scheduled reports",
      listChannels().then((r) => r.channels.filter((c) => !c.problem).map((c) => c.name)),
      (configured) => {
        if (!configured.length) {
          return say(
            "Scheduled reports",
            ["Reports need somewhere to go. Add a channel first (email, Telegram or a folder)."],
            channels,
          )
        }
        choose(
          "How often?",
          REPEATS.map((x) => ({ name: x.name, description: x.description, value: x.value })),
          (v) => jobForm(v as Repeat, configured),
          jobs,
        )
      },
    )
  }

  function jobForm(repeat: Repeat, configured: string[]) {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
    const fields = jobFields(repeat, configured, zone)
    dialog.show(() => (
      <FormDialog
        title="New scheduled report"
        intro={["Runs unattended on the model you use now; the report goes to the channels you name."]}
        form={newForm(fields)}
        submitLabel="schedule"
        onCancel={() => newJob()}
        onSubmit={async (p) => {
          const r = await addJob(
            jobBody(repeat, p.settings as Record<string, string | string[]>, host.model?.()),
          )
          if (!r.ok) return serverErrors(r.detail, fields)
          host.notify(`${r.value.name}: ${r.value.next ? `next ${utcMinute(r.value.next)}` : "scheduled"}`)
          void jobs()
          return null
        }}
      />
    ))
  }

  return { jobs }
}
