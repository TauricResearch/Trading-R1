// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * The report body as the terminal shows it.
 *
 * - Citation markers: the model attaches `[N]` flush to the preceding word (`equipment.[1]`), so
 *   one space goes before each run of markers; the markers stay `[N]` and the report highlighting
 *   (theme/report-markdown.ts) styles them as citations.
 *   Markers side by side get a comma, because markdown reads `[2][3]` as a link and shows `2`.
 * - Dollars and lone tildes: escaped (`\$`, `\~`), so two amounts in a paragraph never read as a
 *   LaTeX span (which leaves everything between them unstyled) and "~" never opens a strikethrough.
 *   Emphasis does not open right after a tilde, so `~**$70B**` is shown as `**~$70B**`. The
 *   highlighting shows the escapes as `$` and `~`. Code spans keep theirs as written.
 *
 * Display-only: the stored/saved report is unchanged. The References list (after the
 * `## References` heading) is left untouched: it is a list of links.
 */

// A run of one or more adjacent `[N]` / `[N, M]` markers (plus whitespace/comma glue), with
// any spaces/tabs immediately before it, normalized to exactly one leading space.
// The marker run itself is captured (group 1) and kept
// verbatim. Newlines are NOT consumed, so a marker at a line start isn't pulled up a line.
// Guards: `(?<!!)` skips image syntax `![1]`; `(?![ \t]*\()` skips a numeric markdown link
// `[1](url)`. Neither is a citation marker.
const CITATION_RUN =
  /(?<!!)[ \t]*(\[\d{1,3}(?:\s*,\s*\d{1,3})*\](?:[ \t]*,?[ \t]*\[\d{1,3}(?:\s*,\s*\d{1,3})*\])*)(?![ \t]*\()/g
// a code span (kept as is), or an unescaped dollar or lone tilde ("~$70B": approximately)
const DOLLAR = /(`[^`\n]*`)|(?<!\\)\$|(?<![\\~])~(?!~)/g

// "~**$70B**": emphasis right after a tilde does not open, so the tilde moves inside it
const TILDE_BOLD = /~\*\*(?=\S)/g

const REFERENCES_HEADING = /(?:^|\n)#{1,6}\s+References\b/i

/** The body with its citation runs spaced and its dollars escaped; References untouched. */
export function formatCitations(text: string): string {
  const at = text.search(REFERENCES_HEADING)
  const body = at >= 0 ? text.slice(0, at) : text
  const rest = at >= 0 ? text.slice(at) : ""
  // code spans (the odd parts) keep their brackets as written
  const spaced = body
    .split(/(`[^`\n]*`)/)
    .map((part, i) =>
      i % 2
        ? part
        : part.replace(CITATION_RUN, (_m, markers) => " " + markers.replace(/\][ \t]*\[/g, "], [")),
    )
    .join("")
  return spaced.replace(TILDE_BOLD, "**~").replace(DOLLAR, (sign, code) => code ?? `\\${sign}`) + rest
}
