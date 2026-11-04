// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Trading-R1 TUI: entry. */
import { render } from "@opentui/solid"

import { App } from "./app"
import { useReportMarkdown } from "./theme/report-markdown"

await useReportMarkdown()
render(App, { exitOnCtrlC: true })
