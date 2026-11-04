// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Input-line history: shell-style Up/Down recall of previously submitted prompts.
 *
 * A tiny in-memory state machine over the current session's prompts. Recall is **per-session**:
 * the caller seeds it with the session's questions (empty for a fresh session, the resumed
 * session's questions on resume) and `record`s each new submission. `up`/`down` return the text
 * to place in the composer, or `null` to let the key fall through to normal cursor movement (so
 * multi-line editing still works; the caller gates on cursor line before asking us). The
 * in-progress draft is saved when you first arrow up and restored when you arrow back past the
 * newest entry.
 *
 * No separate persistence: a session's prompts are its user messages, already stored in the
 * session; this is navigation state over them.
 */
export class InputHistory {
  private entries: string[] = [] // oldest-first
  private cursor: number | null = null // null = editing the live draft, not browsing
  private draft = ""

  /** Seed from the backend's stored prompts (oldest-first). Resets navigation. */
  load(entries: string[]): void {
    this.entries = entries.slice()
    this.reset()
  }

  /** Stop browsing and forget the saved draft. Called internally by `load`/`record`; a caller
   *  may also invoke it to drop a half-browsed draft (e.g. on Escape). */
  reset(): void {
    this.cursor = null
    this.draft = ""
  }

  /** Add a just-submitted prompt (trimmed, non-empty, consecutive-deduped) and reset. */
  record(text: string): void {
    const t = text.trim()
    if (t && this.entries[this.entries.length - 1] !== t) this.entries.push(t)
    this.reset()
  }

  /** Recall the previous (older) entry; `null` = nothing older, let the key pass through. */
  up(current: string): string | null {
    if (this.entries.length === 0) return null
    if (this.cursor === null) {
      this.draft = current // save the in-progress line before browsing
      this.cursor = this.entries.length - 1
    } else if (this.cursor > 0) {
      this.cursor -= 1
    } else {
      return null // already at the oldest
    }
    return this.entries[this.cursor]
  }

  /** Recall the next (newer) entry, or restore the draft past the newest; `null` = pass through. */
  down(): string | null {
    if (this.cursor === null) return null // not browsing: let the key move the cursor
    if (this.cursor < this.entries.length - 1) {
      this.cursor += 1
      return this.entries[this.cursor]
    }
    this.cursor = null // past the newest: back to the live draft
    return this.draft
  }
}
