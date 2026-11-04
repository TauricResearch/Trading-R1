// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * The system's own folder chooser, where there is one: the Finder's on macOS, the desktop's
 * (zenity or kdialog) on Linux with a display. Over ssh or on a machine without a desktop there is
 * none, and the caller asks for a typed folder instead. Pure of the UI: it runs a program and
 * reads what it prints.
 */
export type Pick = { kind: "chosen"; path: string } | { kind: "cancelled" } | { kind: "unavailable" }

export type Run = (cmd: string[]) => Promise<{ code: number; out: string } | null>

async function runProgram(cmd: string[]): Promise<{ code: number; out: string } | null> {
  try {
    const proc = Bun.spawn(cmd, { stdout: "pipe", stderr: "ignore", stdin: "ignore" })
    const out = await new Response(proc.stdout).text()
    return { code: await proc.exited, out }
  } catch {
    return null // the program is not installed
  }
}

/** The commands to try on this system, in order. */
export function pickers(platform: string, env: Record<string, string | undefined>): string[][] {
  if (platform === "darwin") {
    return [["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose a folder for reports")']]
  }
  if (platform === "linux" && (env.DISPLAY || env.WAYLAND_DISPLAY)) {
    return [
      ["zenity", "--file-selection", "--directory", "--title=Choose a folder for reports"],
      ["kdialog", "--getexistingdirectory", env.HOME ?? "/"],
    ]
  }
  return []
}

export async function pickFolder(
  platform: string = process.platform,
  env: Record<string, string | undefined> = process.env,
  run: Run = runProgram,
): Promise<Pick> {
  for (const cmd of pickers(platform, env)) {
    const r = await run(cmd)
    if (r === null) continue // not installed: try the next
    const path = r.out.trim()
    return r.code === 0 && path
      ? { kind: "chosen", path: path.replace(/\/+$/, "") || "/" }
      : { kind: "cancelled" }
  }
  return { kind: "unavailable" }
}
