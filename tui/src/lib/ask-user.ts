// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * ask_user questionnaire: framework-agnostic helpers.
 *
 * `buildOptions` / `recommendedAnswers` are pure so the questionnaire's mapping is
 * unit-testable without a renderer. The model drives clarification mid-run via the
 * `ask_user` tool; the app renders the questions and replies via `answerAskUser`.
 */
import { type AskAnswer, type AskQuestion } from "./api"

/** Sentinel value the "Other" row carries when the user wants to type their own answer.
 *  Prefixed with a Private-Use-Area char so it can never collide with a real option
 *  label (which `buildOptions` uses verbatim as the row value); the "free-text" suffix
 *  keeps the sentinel greppable. */
export const FREE_TEXT_VALUE = "free-text"

interface DialogOption {
  name: string
  value: string
  description: string
}

/** The recommended (first-listed) answer for each question, used to auto-fill when the
 *  client-side deadline expires so the run never blocks on the user. */
export function recommendedAnswers(questions: AskQuestion[]): AskAnswer[] {
  return questions
    .map((q) => ({ id: q.id, question: q.question, answer: q.options[0]?.label ?? "" }))
    .filter((p) => p.answer)
}

/** Map an ask_user question's options into select rows (always + a free-text "Other"). */
export function buildOptions(q: AskQuestion): DialogOption[] {
  const rows: DialogOption[] = q.options.map((o) => ({
    name: o.label,
    value: o.label,
    description: o.description ?? "",
  }))
  rows.push({ name: "Type a different answer…", value: FREE_TEXT_VALUE, description: "" })
  return rows
}
