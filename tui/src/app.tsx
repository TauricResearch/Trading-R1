// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Trading-R1 TUI: the welcome screen and the research shell.
 *
 * Two view states, each with one job:
 *  - welcome:  the wordmark + a centered ask box (idle, inviting the question).
 *  - research: a streaming transcript (query, reasoning, tool calls, answer)
 *              + a slim rail: the plan, data records and sources, with a bottom composer for follow-ups.
 * The first question swaps welcome to research; ^N returns to welcome.
 *
 * When the model calls `ask_user` mid-run (a suspended tool call), the SSE stream
 * carries an `ask_user` event; we pop a DialogSelect questionnaire and reply via
 * `/answer`, resuming the same run. The SSE stream folds into a `Turn` (lib/conversation) whose
 * parts timeline the transcript renders; reasoning/token deltas are coalesced on
 * a ~16ms tick. `/` or `^K` open the command palette (commands, mode, sessions, …).
 */
import { Show, createSignal, onCleanup, onMount } from "solid-js"

import type { ScrollBoxRenderable, TextareaRenderable } from "@opentui/core"
import { useKeyboard, useRenderer } from "@opentui/solid"

import { Composer } from "./components/composer"
import { Footer } from "./components/footer"
import { InputHeader } from "./components/input-header"
import { PlanPanel } from "./components/plan-panel"
import { DataRail, SourcesRail, conversationSources } from "./components/sources-rail"
import { Transcript } from "./components/transcript"
import { Welcome } from "./components/welcome"
import {
  type Health,
  type ModelInfo,
  type StatusInfo,
  answerAskUser,
  compact,
  errorText,
  getSession,
  getSettings,
  getStatus,
  getSync,
  health,
  listCapabilities,
  listModels,
  listSessions,
  researchStream,
  saveBackbone,
  savePrefs,
} from "./lib/api"
import { InputHistory } from "./lib/input-history"
import { contextMeter, mergeUsage } from "./lib/context"
import { day, tokensK } from "./lib/format"
import { loader } from "./lib/load"
import { type SyncStatus, syncGlyph, syncLines } from "./lib/sync"
import { DEFAULT_PREFS, SETTING_ROWS, nextValue, prefsFrom } from "./lib/settings"
import { buildCommandGroups } from "./lib/commands"
import { createBufferedTurnApplier } from "./lib/event-buffer"
import { MODES, type Mode, modeOptions, nextMode, parseMode } from "./lib/mode"
import { modelOptions, pickBackbone, unavailableGuidance } from "./lib/models"
import { type TextPart, type Turn, applyTurnEvent, newTurn, turnsFromSession } from "./lib/conversation"
import { layout } from "./theme"
import { openCapabilityBrowser } from "./ui/capability-browser"
import { CommandPalette } from "./ui/command-palette"
import { DialogProvider, useDialog } from "./ui/dialog"
import { HelpOverlay } from "./ui/help-overlay"
import { InfoDialog } from "./ui/info-dialog"
import { type ListOption, ListDialog } from "./ui/list-dialog"
import { SettingsDialog } from "./ui/settings-dialog"
import { createSetup } from "./setup"
import { PromptDialog } from "./ui/prompt-dialog"
import { runQuestionnaire } from "./ui/questionnaire"

type View = "welcome" | "research"

function AppInner() {
  const dialog = useDialog()
  const renderer = useRenderer()
  const [view, setView] = createSignal<View>("welcome")
  const [turns, setTurns] = createSignal<Turn[]>([])
  const [running, setRunning] = createSignal(false)
  const [syncStatus, setSyncStatus] = createSignal<SyncStatus | null>(null)
  // Launch overrides the CLI passes via env for `trading-r1 --profile/--mode`.
  const launchEnv = typeof process !== "undefined" ? process.env : ({} as Record<string, string | undefined>)
  const launchProfile = launchEnv.TR1_PROFILE || undefined
  const [mode, setMode] = createSignal<Mode>(parseMode(launchEnv.TR1_MODE))
  const [hp, setHp] = createSignal<Health | null>(null)
  const [prefs, setPrefs] = createSignal(DEFAULT_PREFS) // the persisted preferences
  // A brief line in the footer for a confirmation that needs no dialog; it clears itself.
  const [notice, setNotice] = createSignal("")
  let noticeTimer: ReturnType<typeof setTimeout> | undefined
  function notify(text: string) {
    setNotice(text)
    clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => setNotice(""), 4000)
  }
  const [backbone, setBackbone] = createSignal<ModelInfo | null>(null) // /model override
  // The profile every backend call should use: the /model override if set, else the launch
  // profile (`trading-r1 --profile`), else the backend's own default. Keeps status/compact/model on
  // the same backbone the research runs on.
  const activeProfile = () => backbone()?.name ?? launchProfile
  const [usage, setUsage] = createSignal<StatusInfo | null>(null) // rail context meter
  // The composer takes focus at once; a question submitted while the one startup /models fetch
  // (it probes every backbone, up to a few seconds when one is unreachable) is in flight waits
  // for it, so a backbone is always resolved before a run starts.
  const [booting, setBooting] = createSignal(true)
  let bootDone!: () => void
  const booted = new Promise<void>((resolve) => (bootDone = resolve))

  async function refreshUsage() {
    // Needs a selected backbone (the status endpoint requires one); skip until then.
    if (!activeProfile()) return
    // No session yet: the server returns what the first request will carry, and the real window,
    // so the rail meter shows from the start.
    try {
      setUsage(await getStatus(sessionId, activeProfile()))
    } catch {
      /* meter is best-effort */
    }
  }
  // Readiness for the active backbone (default, or the /model-selected one) so the
  // header's online status tracks the model actually in use.
  async function refreshHealth(profile?: string) {
    try {
      setHp(await health(profile))
    } catch {
      /* header shows offline */
    }
  }
  let composerInput: TextareaRenderable | undefined
  let scroller: ScrollBoxRenderable | undefined
  let sessionId: string | undefined
  let abort: AbortController | undefined // aborts the in-flight run (esc to cancel)
  const inputHistory = new InputHistory() // shell-style Up/Down prompt recall

  const lastTurn = (): Turn | null => {
    const ts = turns()
    return ts.length ? ts[ts.length - 1] : null
  }
  const updateLast = (fn: (t: Turn) => Turn) =>
    setTurns((ts) => (ts.length ? [...ts.slice(0, -1), fn(ts[ts.length - 1])] : ts))

  onMount(() => void bootstrap())
  onMount(() => {
    if (launchEnv.TR1_LAUNCH_NOTICE) notify(launchEnv.TR1_LAUNCH_NOTICE)
  })
  // A backbone that did not answer its readiness probe (the endpoint was still starting, a
  // blip) is probed again until it does, so the header does not stay offline for good.
  onMount(() => {
    const timer = setInterval(() => {
      if (!ready() && !running() && !booting()) void refreshHealth(activeProfile())
    }, 10_000)
    onCleanup(() => clearInterval(timer))
  })

  // Startup: input is disabled until this resolves, then a backbone is auto-selected (no
  // popup). Preference: the launch --profile (respected even if offline), else the first
  // *ready* backbone, else the server's auto-default, else the first; the user switches with
  // /model. A seeded prompt (`trading_r1 "query"`) runs once the backbone is selected.
  async function bootstrap() {
    const seed = (typeof process !== "undefined" ? process.env.TR1_INITIAL_PROMPT : "")?.trim()
    // Load models + the persisted settings together (best-effort: a backend that isn't up yet
    // leaves the picker to auto-select and health offline).
    const [modelsRes, settingsRes] = await Promise.allSettled([listModels(), getSettings()])
    const data = modelsRes.status === "fulfilled" ? modelsRes.value : undefined
    const persisted = settingsRes.status === "fulfilled" ? settingsRes.value.backbone : undefined
    if (settingsRes.status === "fulfilled") {
      const p = prefsFrom(settingsRes.value)
      setPrefs(p)
      if (settingsRes.value.problem)
        notify(`Settings not used, session sync is off: ${settingsRes.value.problem}`)
      // a launch flag or TR1_MODE wins over the saved starting mode
      if (!launchEnv.TR1_MODE) setMode(p.default_mode)
    }
    setBooting(false) // a waiting question may go now
    bootDone()
    const picked = pickBackbone(data?.models ?? [], {
      launch: launchProfile,
      saved: persisted,
      serverDefault: data?.default ?? null,
      defaultReason: data?.default_reason ?? null,
    })
    if (data && picked) {
      const { chosen } = picked
      setBackbone(chosen)
      if (picked.supplied) {
        // say so once, and keep it: a key added later must not move the user to another provider
        const using = `Using ${chosen.provider_label} (${chosen.model}), the one you set up.`
        saveBackbone(chosen.name).then(
          () => notify(`${using} Change it in Model…`),
          (e) => notify(`${using} It could not be kept as your default: ${errorText(e)}`),
        )
      }
      void refreshHealth(chosen.name)
      void refreshUsage()
      if (data.models.some((x) => x.name === "tauric" && !x.configured) && chosen.ready)
        notify("Market data needs a Tauric key: add it under API keys…")
      // nothing can answer yet (no key, no server): open the picker, whose rows say what to do
      if (!seed && !launchProfile && !data.models.some((x) => x.ready))
        void chooseModel("No model is ready. Set one up")
    } else {
      void refreshHealth(launchProfile)
      if (data && !seed && !launchProfile) {
        dialog.show(() => (
          <InfoDialog
            title="No model provider set up"
            lines={["Get a Tauric key at https://tauric.ai/signup, or choose another provider next."]}
            onClose={() => void chooseModel("Choose a model provider")}
          />
        ))
      }
    }
    if (seed) void ask(seed) // run the seed now that a backbone is selected (or the server defaults)
  }

  function newResearch() {
    if (running()) return
    setTurns([])
    inputHistory.load([]) // input recall is per-session: a new session starts empty
    sessionId = undefined
    setView("welcome")
  }

  async function quit() {
    await renderer.destroy()
    process.exit(0)
  }

  function openHelp() {
    dialog.show(() => <HelpOverlay onClose={() => dialog.close()} />)
  }

  /** A small read-only result/confirmation modal. */
  function info(title: string, lines: string[]) {
    dialog.show(() => <InfoDialog title={title} lines={lines} onClose={() => dialog.close()} />)
  }

  // A key was stored or removed: the model in use may now answer (or the default has changed).
  async function keysChanged() {
    let data: Awaited<ReturnType<typeof listModels>>
    try {
      data = await listModels()
    } catch (e) {
      return notify(`Couldn't refresh the model list: ${errorText(e)}`)
    }
    const current = data.models.find((x) => x.name === backbone()?.name)
    const next = current?.ready ? current : data.models.find((x) => x.name === data.default && x.ready)
    if (!next) return
    if (next.name !== backbone()?.name) notify(`Using ${next.provider_label} (${next.model})`)
    setBackbone(next)
    void refreshHealth(next.name)
    void refreshUsage()
  }
  const load = loader(info)
  const setup = createSetup({
    dialog,
    info,
    notify,
    model: activeProfile,
    onKeysChanged: () => void keysChanged(),
  })

  const lastAnswer = (): string => {
    const t = lastTurn()
    if (!t) return ""
    for (let i = t.parts.length - 1; i >= 0; i--) {
      if (t.parts[i].kind === "text") return (t.parts[i] as TextPart).text
    }
    return ""
  }

  // ----- palette commands ------------------------------------------------- //

  // /copy: the answer to the system clipboard via OSC 52 (works over SSH).
  function copyAnswer() {
    const text = lastAnswer()
    if (!text) return
    const b64 = Buffer.from(text, "utf8").toString("base64")
    process.stdout.write(`\x1b]52;c;${b64}\x07`)
    notify("Answer copied to the clipboard")
  }

  // /export: the report as a page (html), as markdown, and the full trajectory (json), to cwd.
  function exportRun() {
    if (!lastTurn()?.trajectory || !sessionId)
      return info("Export report", ["No answered turn to export yet."])
    setup.exportReport(sessionId)
  }

  // Send report: the last answer through the channels the owner picks.
  function sendRun() {
    if (!lastTurn()?.trajectory || !sessionId) return info("Send report", ["No answered turn yet."])
    void setup.sendReport(sessionId)
  }

  // Session status: the model and the context usage of the active session.
  function showStatus() {
    return load("Session status", getStatus(sessionId, activeProfile()), (st) =>
      info("Session status", [
        `Model:    ${backbone()?.model ?? st.model}`,
        `Context:  ${st.used_pct}%  (${tokensK(st.tokens_used)} / ${tokensK(st.context_window)})`,
        `Turns:    ${st.turns}`,
      ]),
    )
  }

  // Model: switch the model. Only *ready* rows are selectable; a disabled row explains its fix
  // (set a key / start the server). A model is already auto-selected at startup.
  function chooseModel(title = "Model") {
    return load("Model", listModels(), (data) => pickModel(title, data))
  }

  function selectModel(m: ModelInfo | null) {
    setBackbone(m) // switch this run immediately, never blocked on persistence
    void refreshHealth(m?.name) // header tracks the now-active backbone
    void refreshUsage()
    if (!m) return
    // Persist the sticky choice. If the settings store is unwritable the switch still holds
    // for this run, and the message says so.
    saveBackbone(m.name).then(
      () => notify(`Model: ${m.provider_label} (${m.model}), saved as your default`),
      (e) =>
        info("Model", [
          `Model set to ${m.provider_label} (${m.model}) for this run.`,
          `Couldn't save it as your default: ${errorText(e)}`,
        ]),
    )
  }

  /** The key of a model chosen without a working one: saved, checked, and the model in use. */
  function askKey(m: ModelInfo, title: string) {
    setup.keyFor(
      { provider_label: m.provider_label ?? m.name, model: m.model, api_key_env: m.api_key_env! },
      {
        back: () => void chooseModel(title),
        use: async () => {
          const fresh = (await listModels()).models.find((x) => x.name === m.name) ?? m
          if (fresh.reason === "key_refused") return "That key was refused"
          if (!fresh.ready) return "Key saved, but this model is not answering. Check the key, or try later"
          dialog.close()
          selectModel(fresh)
          return null
        },
      },
    )
  }

  function pickModel(title: string, data: Awaited<ReturnType<typeof listModels>>) {
    const options = modelOptions(data.models, activeProfile(), data.default)
    dialog.show(() => (
      <ListDialog
        title={title}
        options={options}
        onCancel={() => dialog.close()}
        onSelect={(name) => {
          dialog.close()
          selectModel(data.models.find((x) => x.name === name) ?? null)
        }}
        onSelectDisabled={(name) => {
          const m = data.models.find((x) => x.name === name)
          if (m?.api_key_env && (!m.configured || m.reason === "key_refused")) return askKey(m, title)
          if (m)
            info(
              m.reason === "key_refused"
                ? "API key refused"
                : m.configured
                  ? "Model offline"
                  : "Model needs an API key",
              unavailableGuidance(m),
            )
        }}
      />
    ))
  }

  // Settings: the preferences, each saved as it changes; the dialog stays open on its row.
  function openSettings(at = 0) {
    const refreshSync = () => getSync().then(setSyncStatus, () => setSyncStatus(null))
    void refreshSync()
    dialog.show(() => (
      <SettingsDialog
        prefs={prefs}
        selected={at}
        extra={(key) => (key === "sync" && syncStatus() ? syncLines(syncStatus()!) : [])}
        onClose={() => dialog.close()}
        onSelect={(key) => {
          if (key === "notes")
            return void setup.openNotes(() => openSettings(SETTING_ROWS.findIndex((r) => r.key === "notes")))
          // after this keypress: opened now, the list would take the same enter as its first row
          if (key === "sync_more") return void setTimeout(setup.syncMore, 0)
          const change = nextValue(key, prefs())
          setPrefs({ ...prefs(), ...change }) // holds for this run even if persisting fails
          savePrefs(change)
            .then(() => (key === "sync" ? refreshSync() : undefined))
            .catch((e) => notify(`Couldn't save the setting: ${errorText(e)}`))
        }}
      />
    ))
  }

  // Browse tools & skills: fetch the catalog, then hand off to the drill-down browser
  // (ui/capability-browser). The navigation itself lives there; app.tsx owns only the fetch.
  function browseCapabilities() {
    return load("Tools & skills", listCapabilities(), (catalog) => openCapabilityBrowser(dialog, catalog))
  }

  // /compact: summarize older turns server-side, freeing context.
  function runCompact() {
    if (running()) return // don't compact a session mid-turn (matches newResearch/sessions)
    if (!sessionId) return
    dialog.show(() => (
      <PromptDialog
        title="Compact context"
        prompt="What should the summary preserve? Leave empty for the default."
        placeholder="e.g. keep every ticker and figure"
        onCancel={() => dialog.close()}
        onSubmit={async (instruction) => {
          dialog.close()
          // Show a live "compacting…" line in the transcript (the summary is a multi-second
          // model call), then swap in the result.
          const label = instruction ? `Compact context: ${instruction}` : "Compact context"
          const liveLine = `Compacting context: ${tokensK(usage()?.tokens_used ?? 0)} tokens…`
          setTurns((ts) => [
            ...ts,
            { ...newTurn(label, "running"), parts: [{ kind: "status", text: liveLine, running: true }] },
          ])
          setRunning(true) // block a racing research turn while we compact
          try {
            const r = await compact(sessionId!, instruction || undefined, activeProfile())
            const text = r.compacted
              ? `Compacted context: ${tokensK(r.tokens_before ?? 0)} to ${tokensK(r.tokens_after ?? 0)} tokens`
              : `Nothing to compact${r.reason ? `: ${r.reason}` : ""}`
            updateLast((t) => ({
              ...t,
              status: "answered",
              phase: undefined,
              parts: [{ kind: "status", text, running: false }],
            }))
            if (r.compacted) void refreshUsage() // context freed: refresh the meter
          } catch (e) {
            updateLast((t) => ({ ...t, status: "error", phase: undefined, error: errorText(e) }))
          } finally {
            setRunning(false)
          }
        }}
      />
    ))
  }

  // /sessions: pick a saved session and rehydrate the transcript to resume it.
  function chooseSession() {
    if (running()) return
    return load("Resume session", listSessions(), showSessions)
  }

  function showSessions(sessions: Awaited<ReturnType<typeof listSessions>>) {
    if (!sessions.length)
      return info("Resume session", ["No saved sessions yet.", "Ask a question to start one."])
    const options: ListOption[] = sessions.map((s) => ({
      name: `${syncGlyph(s.sync)} ${s.title}`,
      description: `${s.turns} turn${s.turns === 1 ? "" : "s"}, ${day(s.updated_at)}`,
      value: s.id,
    }))
    dialog.show(() => (
      <ListDialog
        title="Resume session"
        options={options}
        onCancel={() => dialog.close()}
        onSelect={(id) => {
          dialog.close()
          void load("Resume session", getSession(id), (entries) => {
            sessionId = id
            setTurns(turnsFromSession(entries))
            // recall THIS session's prompts, the questions as typed
            inputHistory.load(
              entries.flatMap((e) => (e.type === "turn" && e.question.trim() ? [e.question.trim()] : [])),
            )
            setView("research")
            void refreshUsage()
          })
        }}
      />
    ))
  }

  // Mode: choose how the agent works this session (shift+tab cycles).
  function chooseMode() {
    dialog.show(() => (
      <ListDialog
        title="Mode"
        options={modeOptions(mode())}
        selected={MODES.indexOf(mode())}
        onCancel={() => dialog.close()}
        onSelect={(m) => {
          dialog.close()
          setMode(parseMode(m))
        }}
      />
    ))
  }

  function openPalette() {
    const groups = buildCommandGroups(
      {
        newSession: newResearch,
        resumeSession: chooseSession,
        compactContext: runCompact,
        sessionStatus: showStatus,
        chooseModel: () => void chooseModel(),
        chooseMode,
        copyAnswer,
        exportReport: exportRun,
        sendReport: sendRun,
        sendChannels: () => void setup.channels(),
        scheduledReports: () => void setup.jobs(),
        browseTools: browseCapabilities,
        help: openHelp,
        apiKeys: () => void setup.apiKeys(),
        backgroundService: () => void setup.service(),
        settings: () => openSettings(),
        quit,
      },
      {
        mode: mode(),
        model: model(),
        running: running(),
        hasAnswer: !running() && !!lastAnswer(),
        hasSession: !!sessionId && turns().length > 0,
      },
    )
    dialog.show(() => (
      <CommandPalette
        groups={groups}
        onRun={(c) => {
          dialog.close()
          c.run()
        }}
        onCancel={() => dialog.close()}
      />
    ))
  }

  useKeyboard((e) => {
    if (dialog.open()) return
    if (e.name === "escape" && running())
      abort?.abort() // cancel the running turn
    else if (e.ctrl && e.name === "n") newResearch()
    else if (e.ctrl && e.name === "k") openPalette()
    else if (e.name === "backtab" || (e.name === "tab" && e.shift)) setMode(nextMode(mode()))
    else if (e.name === "pageup") scroller?.scrollBy(-12)
    else if (e.name === "pagedown") scroller?.scrollBy(12)
    // Only plain (unmodified) Up/Down recall history; Shift/Ctrl/Meta/Option arrows keep
    // OpenTUI's selection + word/buffer navigation.
    else if (
      (e.name === "up" || e.name === "down") &&
      composerFocused() &&
      !e.ctrl &&
      !e.shift &&
      !e.meta &&
      !e.option &&
      !e.super &&
      !e.hyper
    )
      recallHistory(e.name, () => e.preventDefault())
  })

  // Up/Down recall previous prompts, but only when the cursor is on the first (Up) or last
  // (Down) line, so multi-line editing still moves the cursor within the draft. preventDefault
  // stops the textarea from also moving the cursor when we do recall.
  function recallHistory(dir: "up" | "down", preventDefault: () => void) {
    const input = composerInput
    if (!input) return
    const text = input.plainText
    const off = input.cursorOffset
    if (dir === "up" && text.slice(0, off).includes("\n")) return // not on the first line
    if (dir === "down" && text.slice(off).includes("\n")) return // not on the last line
    const recalled = dir === "up" ? inputHistory.up(text) : inputHistory.down()
    if (recalled === null) return // nothing to recall: let the key move the cursor
    input.setText(recalled)
    input.cursorOffset = recalled.length // cursor at the end, ready to edit or resubmit
    preventDefault()
  }

  // Typing `/` into an empty composer opens the palette.
  function onComposerInput(value: string) {
    if (value === "/" && !dialog.open()) {
      if (composerInput) composerInput.clear()
      openPalette()
    }
  }

  async function ask(raw: string) {
    const q = raw.trim()
    if (!q || running()) return
    if (booting()) {
      await booted
      if (running()) return
    }
    inputHistory.record(q) // add to this session's Up/Down recall + reset browsing
    // A backbone is auto-selected at startup; if the backend was unreachable then, we send no
    // profile and the server uses its auto-default. Either way there's always something to run.
    if (composerInput) composerInput.clear() // clear the field once submitted
    const activeMode = mode()
    const fromWelcome = view() === "welcome"
    setRunning(true)
    abort = new AbortController() // esc cancels this run (aborts the SSE fetch)
    // A fresh session starts a new id; follow-ups reuse it so the backend threads
    // the prior turns' history.
    if (fromWelcome || !sessionId) sessionId = crypto.randomUUID()
    // Immediate feedback: swap to the research view and append the running turn the
    // instant the user hits enter.
    setView("research")
    setTurns((ts) => [...ts, newTurn(q)])
    try {
      const apply = (ev: Parameters<typeof applyTurnEvent>[1]) => updateLast((t) => applyTurnEvent(t, ev))
      // Coalesce reasoning/token deltas on a ~16ms tick; non-delta events flush
      // first so the transcript keeps arrival order.
      const buffered = createBufferedTurnApplier(apply)
      try {
        for await (const ev of researchStream(
          {
            question: q,
            mode: activeMode,
            sessionId,
            profile: activeProfile(),
          },
          { signal: abort.signal },
        )) {
          // Live context meter: fold the per-round usage estimate straight into the rail
          // (it's session state, not a transcript event, so it bypasses the turn applier).
          if (ev.event === "usage") {
            setUsage((u) => mergeUsage(u, ev.data, backbone()?.model ?? ""))
            continue
          }
          // Mid-run clarification: the model's ask_user call is suspended server-side.
          // Render the questionnaire, then POST the answers to resume the same run. The
          // stream is idle (server blocked) while we ask, so awaiting here is safe.
          if (ev.event === "ask_user") {
            buffered.flush() // land any pending deltas before the modal
            const d = ev.data
            const answers = await runQuestionnaire(dialog, d.questions, d.timeout_s)
            await answerAskUser(d.request_id, answers).catch((e) =>
              notify(`Your answer was not delivered: ${errorText(e)}`),
            )
            continue
          }
          buffered.handle(ev)
        }
      } finally {
        buffered.clear()
        buffered.flush()
      }
    } catch (err) {
      if (abort?.signal.aborted) updateLast((t) => ({ ...t, status: "cancelled" }))
      else updateLast((t) => ({ ...t, status: "error", error: errorText(err) }))
    } finally {
      abort = undefined
      setRunning(false)
      void refreshUsage() // update the rail context meter once the turn settles
      void refreshHealth(activeProfile())
    }
  }

  const model = () => backbone()?.model ?? hp()?.backbone?.model ?? "-"
  const ready = () => hp()?.status === "ok"
  const version = () => hp()?.version ?? "0.1.0"
  const composerFocused = () => !dialog.open()

  return (
    <box flexDirection="column" flexGrow={1}>
      <box flexGrow={1} minHeight={0} flexDirection="column">
        <Show
          when={view() === "research" && turns().length > 0}
          fallback={
            <Welcome
              onSubmit={ask}
              mode={mode()}
              model={model()}
              ready={ready()}
              focused={composerFocused()}
              onInput={onComposerInput}
              inputRef={(r) => (composerInput = r)}
            />
          }
        >
          <box
            flexGrow={1}
            flexDirection="column"
            gap={1}
            paddingLeft={layout.pad.docked.x}
            paddingRight={layout.pad.docked.x}
            paddingTop={layout.pad.docked.y}
          >
            <box flexGrow={1} minHeight={0} flexDirection="row" gap={1}>
              <Transcript
                turns={turns()}
                scrollRef={(r) => (scroller = r)}
                showReasoning={prefs().show_reasoning}
                toolDetails={prefs().tool_details}
              />
              <Show when={prefs().side_panel}>
                <box
                  width="20%"
                  minWidth={24}
                  flexShrink={0}
                  minHeight={0}
                  overflow="hidden"
                  flexDirection="column"
                  gap={1}
                >
                  <PlanPanel plan={lastTurn()!.plan} />
                  <DataRail turn={lastTurn()!} />
                  <SourcesRail turn={lastTurn()!} sources={conversationSources(turns())} usage={usage()} />
                </box>
              </Show>
            </box>
            <box flexDirection="column" flexShrink={0}>
              <InputHeader mode={mode()} model={model()} ready={ready()} />
              <Composer
                onSubmit={ask}
                focused={composerFocused()}
                placeholder="ask a follow-up, or / for commands"
                onInput={onComposerInput}
                inputRef={(r) => (composerInput = r)}
              />
            </box>
          </box>
        </Show>
      </box>
      <Footer
        version={version()}
        notice={notice()}
        context={prefs().side_panel ? undefined : contextMeter(usage())}
      />
      <box height={1} flexShrink={0} />
    </box>
  )
}

export function App() {
  return (
    <DialogProvider>
      <AppInner />
    </DialogProvider>
  )
}
