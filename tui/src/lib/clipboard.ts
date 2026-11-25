// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Text to the clipboard by the first way that applies, in the order of Neovim's clipboard
 * providers: on this machine (not over SSH), the platform's tool (pbcopy, wl-copy, xclip); inside
 * tmux, tmux's own buffer, which tmux also sends to the terminal's clipboard (`load-buffer -w`,
 * which works under tmux's default `set-clipboard external`); else the terminal's OSC 52.
 */

type Env = Readonly<Record<string, string | undefined>>

export type Ways = {
  env: Env
  platform: NodeJS.Platform
  /** Whether a command is on PATH. */
  has: (command: string) => boolean
  /** Run a command with `input` on stdin; whether it exited 0. */
  run: (cmd: string[], input: string) => boolean
  /** Write OSC 52; false when the terminal is known not to take it. */
  osc52: (text: string) => boolean
}

/** What one OSC 52 sequence may carry (as most terminals cap it). */
export const OSC52_MAX_BYTES = 100_000

/** The platform's clipboard tool for a local session, if there is one. */
function localTool({ env, platform, has }: Ways): string[] | undefined {
  if (env.SSH_CONNECTION || env.SSH_TTY || env.SSH_CLIENT) return undefined
  if (platform === "darwin") return ["pbcopy"]
  if (env.WAYLAND_DISPLAY && has("wl-copy")) return ["wl-copy"]
  if (env.DISPLAY && has("xclip")) return ["xclip", "-selection", "clipboard"]
  return undefined
}

/** Whether `text` was copied, by the first way that applies. */
export function copyText(text: string, ways: Ways): boolean {
  const tool = localTool(ways)
  if (tool) return ways.run(tool, text)
  if (ways.env.TMUX) return ways.run(["tmux", "load-buffer", "-w", "-"], text)
  if (Buffer.byteLength(text, "utf8") * (4 / 3) > OSC52_MAX_BYTES) return false
  return ways.osc52(text)
}

/** The notice for a copy. */
export function copiedText(copied: boolean): string {
  return copied ? "Answer copied" : "Answer not copied: /export saves it"
}

/** The ways of this process: real commands, and OSC 52 through the renderer. */
export function systemWays(osc52: (text: string) => boolean): Ways {
  return {
    env: process.env,
    platform: process.platform,
    has: (command) => Bun.which(command) !== null,
    run: (cmd, input) => {
      try {
        const done = Bun.spawnSync({
          cmd,
          stdin: Buffer.from(input, "utf8"),
          stdout: "ignore",
          stderr: "ignore",
          timeout: 5_000,
        })
        return done.exitCode === 0
      } catch {
        return false
      }
    },
    osc52,
  }
}
