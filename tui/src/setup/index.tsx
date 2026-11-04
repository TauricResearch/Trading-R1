// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Setup flows: where reports go and which model keys are stored, as dialogs over the backend's
 * management API. Each flow ends by returning to the list it came from, so a change is seen where
 * it was made. Secrets are typed into the form dialog (never shown) and sent write-only.
 */
import { createChannels } from "./channels"
import { type SetupHost, createFlow } from "./flow"
import { createJobs } from "./jobs"
import { createKeys } from "./keys"
import { createNotes } from "./notes"
import { createReportExport } from "./report-export"
import { createSend } from "./send"
import { createService } from "./service"
import { createSync } from "./sync"

export function createSetup(host: SetupHost) {
  const flow = createFlow(host)
  const { channels, addThen } = createChannels(flow)
  return {
    channels,
    ...createKeys(flow),
    ...createReportExport(flow),
    ...createSend(flow, addThen),
    ...createJobs(flow, channels),
    ...createService(flow),
    ...createSync(flow),
    ...createNotes(host),
  }
}
