// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Remembered notes: what the agent noted about the user; pick one to forget it. */
import { errorText, forgetNote, listNotes } from "../lib/api"
import { loader } from "../lib/load"
import { layout } from "../theme"
import { ListDialog } from "../ui/list-dialog"
import type { SetupHost } from "./flow"

export function createNotes({ dialog, info, notify }: SetupHost) {
  const load = loader(info)

  function openNotes(back: () => void) {
    return load("Remembered notes", listNotes(), (notes) => showNotes(notes, back))
  }

  function showNotes(notes: Awaited<ReturnType<typeof listNotes>>, back: () => void) {
    if (!notes.length) {
      return info("Remembered notes", [
        "Nothing is remembered yet.",
        "The agent saves a note when you ask it to remember something in a chat session.",
      ])
    }
    dialog.show(() => (
      <ListDialog
        title="Remembered notes"
        width={layout.width.lg}
        options={notes.map((n) => ({
          name: n.description,
          description: `${n.type}, updated ${n.updated}`,
          value: n.name,
        }))}
        confirm="open"
        escape="back"
        onCancel={back}
        onSelect={(name) => {
          const note = notes.find((n) => n.name === name)!
          dialog.show(() => (
            <ListDialog
              title={note.description}
              options={[
                {
                  name: "Forget this note",
                  description: "The agent will no longer see it.",
                  value: "forget",
                },
                { name: "Keep it", description: "Leave it as it is.", value: "keep" },
              ]}
              escape="back"
              onCancel={() => void openNotes(back)}
              onSelect={(choice) => {
                if (choice !== "forget") return void openNotes(back)
                forgetNote(name)
                  .then(
                    () => notify(`Forgot: ${note.description}`),
                    (e) => notify(`Couldn't forget the note: ${errorText(e)}`),
                  )
                  .finally(() => void openNotes(back))
              }}
            />
          ))
        }}
      />
    ))
  }

  return { openNotes }
}
