// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * How the report's inline markdown is highlighted: OpenTUI's markdown_inline query with two
 * changes (theme/markdown_inline.scm):
 * - citation markers `[N]` keep their brackets and are styled as citations (the stock query
 *   conceals the brackets);
 * - an escaped dollar shows as a dollar. Two dollar amounts in one paragraph (`$72B … $47.6B`)
 *   otherwise read as a LaTeX span, leaving the citations and emphasis inside it unstyled; the
 *   display escapes dollars (`formatCitations`) and this rule shows them plainly.
 */
import { dirname, join } from "node:path"

import { getTreeSitterClient } from "@opentui/core"

export async function useReportMarkdown(): Promise<void> {
  const client = getTreeSitterClient()
  await client.initialize() // the defaults register on initialize; this one replaces theirs
  const core = dirname(Bun.resolveSync("@opentui/core", import.meta.dir))
  client.addFiletypeParser({
    filetype: "markdown_inline",
    queries: { highlights: [join(import.meta.dir, "markdown_inline.scm")] },
    wasm: join(core, "assets/markdown_inline/tree-sitter-markdown_inline.wasm"),
  })
  await client.preloadParser("markdown_inline") // loaded before the first report is drawn
}
