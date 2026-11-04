// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * ask_user questionnaire: renders the model's mid-run clarifying questions one at a time
 * and resolves to the chosen answers. The model's ask_user tool call is suspended on the
 * server while this runs; the answers go back via `answerAskUser` and the run resumes.
 *
 * A whole-flow deadline falls back to the recommended (first) option, and skipping/esc-ing a
 * question also takes its recommended answer, so a mid-run question never strands the suspended run.
 */
import type { AskAnswer, AskQuestion } from "../lib/api"
import { recommendedAnswers } from "../lib/ask-user"
import type { DialogApi } from "./dialog"
import { DialogSelect } from "./dialog-select"

export type QResult = { kind: "answer"; value: string } | { kind: "skip" } | { kind: "timeout" }

/** Ask one question and resolve how it settled. The default renders a `DialogSelect`; the
 *  orchestration (`runQuestionnaire`) takes it as a seam so its deadline/skip/timeout
 *  fallbacks are unit-testable without a renderer. */
export type AskOne = (
  dialog: DialogApi,
  q: AskQuestion,
  index: number,
  total: number,
  deadlineMs: number,
) => Promise<QResult>

/** Show one question as a modal; resolves to the answer, a skip, or a timeout. */
function askOne(
  dialog: DialogApi,
  q: AskQuestion,
  index: number,
  total: number,
  deadlineMs: number,
): Promise<QResult> {
  return new Promise((resolve) => {
    dialog.show(() => (
      <DialogSelect
        q={q}
        index={index}
        total={total}
        deadlineMs={deadlineMs}
        onSubmit={(a) => {
          dialog.close()
          resolve(a ? { kind: "answer", value: a } : { kind: "skip" })
        }}
        onCancel={() => {
          dialog.close()
          resolve({ kind: "skip" })
        }}
        onTimeout={() => {
          dialog.close()
          resolve({ kind: "timeout" })
        }}
      />
    ))
  })
}

export async function runQuestionnaire(
  dialog: DialogApi,
  questions: AskQuestion[],
  timeoutS: number,
  ask: AskOne = askOne,
  now: () => number = Date.now,
): Promise<AskAnswer[]> {
  const deadline = now() + timeoutS * 1000
  const answers: AskAnswer[] = []
  for (let i = 0; i < questions.length; i++) {
    const q = questions[i]
    if (now() >= deadline) {
      answers.push(...recommendedAnswers(questions.slice(i)))
      break
    }
    const r = await ask(dialog, q, i, questions.length, deadline)
    if (r.kind === "timeout") {
      answers.push(...recommendedAnswers(questions.slice(i)))
      break
    }
    // an explicit answer, or the recommended (first) option on skip/esc; never strand it
    const answer = r.kind === "answer" ? r.value : (q.options[0]?.label ?? "")
    if (answer) answers.push({ id: q.id, question: q.question, answer })
  }
  return answers
}
