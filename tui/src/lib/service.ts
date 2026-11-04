// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/** The background service: what its state means to the person, in words. Pure. */
import type { ServiceState } from "./api"

type ServiceAction = "install" | "remove" | "none"

/** A short headline (a list row is about 55 characters wide) and what the one action does. */
export function serviceView(s: ServiceState): { headline: string; detail: string; action: ServiceAction } {
  if (!s.supported) {
    return {
      headline: "Not available here",
      detail: "It needs systemd (Linux) or launchd (macOS).",
      action: "none",
    }
  }
  if (!s.installed) {
    return {
      headline: "Reports run only while this terminal is open",
      detail: "keeps them going when it is closed",
      action: "install",
    }
  }
  if (s.managed) {
    return {
      headline: "Running as a background service",
      detail: "from a shell: trading-r1 daemon uninstall",
      action: "remove",
    }
  }
  return {
    headline: s.active
      ? "Installed; takes over when this terminal ends"
      : "Installed; starts at your next login",
    detail: "stop keeping it running",
    action: "remove",
  }
}

export function installNotes(copies: string[]): string[] {
  return [
    "This installs a per-user service that runs the same backend and starts when you log in. It takes over once the terminal's own backend has ended.",
    "",
    copies.length
      ? `These variables from the backend's environment are copied to a file only you can read: ${copies.join(", ")}.`
      : "No API key variables are set in the environment. Keys stored under API keys… are read from their own private file.",
    "",
    "On Linux it starts at boot and keeps running after you log out (install turns on lingering where allowed). On macOS it runs while you are logged in.",
  ]
}
