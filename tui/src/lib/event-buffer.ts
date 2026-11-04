// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** Event buffer: coalesces high-frequency SSE reasoning/token deltas into ordered turn updates. */
import type { SSEEvent } from "./api"

interface BufferedTurnApplier {
  handle(ev: SSEEvent): void
  flush(): void
  clear(): void
}

/** Coalesce high-frequency reasoning/token deltas while preserving event order. */
export function createBufferedTurnApplier(apply: (ev: SSEEvent) => void, delayMs = 16): BufferedTurnApplier {
  let reasonBuf = ""
  let tokenBuf = ""
  let timer: ReturnType<typeof setTimeout> | null = null

  const flush = () => {
    timer = null
    if (reasonBuf) {
      apply({ event: "reasoning", data: { text: reasonBuf } })
      reasonBuf = ""
    }
    if (tokenBuf) {
      apply({ event: "token", data: { text: tokenBuf } })
      tokenBuf = ""
    }
  }

  const clear = () => {
    if (timer) clearTimeout(timer)
    timer = null
  }

  const schedule = () => {
    if (!timer) timer = setTimeout(flush, delayMs)
  }

  return {
    handle(ev: SSEEvent) {
      if (ev.event === "reasoning") {
        reasonBuf += (ev.data as { text: string }).text
        schedule()
      } else if (ev.event === "token") {
        tokenBuf += (ev.data as { text: string }).text
        schedule()
      } else {
        clear()
        flush()
        apply(ev)
      }
    },
    flush,
    clear,
  }
}
