// The desktop app (desktop/) loads this same frontend and adds one native
// ability: connecting the computer it runs on as a device. This is the page's side
// of that bridge; outside the app desktopBridge() is null and the page offers
// the download instead.
import { reactive } from 'vue'

import { ApiError, connectDevice, deviceProposedName, listMyDevices } from '../api'

import { desktopCan } from './desktopApp'

// The site serves the desktop build itself: build.yml copies the `desktop-latest`
// release (.github/workflows/desktop.yml) into the frontend image, because GitHub
// release downloads are unreliable from mainland networks.
const RELEASE = '/downloads/desktop'
export const DOWNLOADS = [
  { os: 'mac', labelKey: 'global.desktop.macAppleSilicon', href: `${RELEASE}/Cheese-arm64.dmg` },
  { os: 'mac', labelKey: 'global.desktop.macIntel', href: `${RELEASE}/Cheese-x64.dmg` },
  { os: 'windows', labelKey: 'global.desktop.windows', href: `${RELEASE}/Cheese-Setup-x64.exe` },
] as const

export type Download = (typeof DOWNLOADS)[number]

// The visitor's own system goes first; a browser does not say which Mac chip it runs on.
export function downloadsForThisComputer() {
  const own = /Windows/.test(navigator.userAgent) ? 'windows' : 'mac'
  return [...DOWNLOADS.filter((d) => d.os === own), ...DOWNLOADS.filter((d) => d.os !== own)]
}

interface UserAgentData {
  getHighEntropyValues?: (hints: string[]) => Promise<{ architecture?: string }>
}

/** The build for the computer this page runs on. Chromium says which chip a Mac
 *  has; Safari does not, and most Macs sold now have Apple's. */
export async function downloadForThisComputer(): Promise<Download> {
  if (/Windows/.test(navigator.userAgent)) return DOWNLOADS[2]
  const agent = (navigator as unknown as { userAgentData?: UserAgentData }).userAgentData
  const hints = await agent?.getHighEntropyValues?.(['architecture']).catch(() => null)
  return hints?.architecture === 'x86' ? DOWNLOADS[1] : DOWNLOADS[0]
}

/** A step of connecting this computer, as the app reports it. */
export type ConnectStep = 'removeOld' | 'tools' | 'download' | 'approve' | 'runtime' | 'start'

/** Why connecting stopped: the step it stopped at, or "cancelled", and what the tool said. */
export interface ConnectFailure {
  step: ConnectStep | 'cancelled' | 'busy' | 'prepare' | 'login'
  detail: string
}

type ClaudeLoginStep = 'preparing' | 'browser'

type Progress =
  | { kind: 'step'; id: ConnectStep | ClaudeLoginStep }
  | { kind: 'percent'; value: number }
  | { kind: 'code'; text: string }

interface TauriChannel<T> {
  onmessage: (message: T) => void
}

interface TauriGlobal {
  core: {
    invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>
    Channel: new <T>() => TauriChannel<T>
  }
}

export interface ConnectOptions {
  knownDeviceIds: string[]
  approve: (code: string) => Promise<void>
  onStep: (step: ConnectStep) => void
  onPercent: (value: number) => void
}

export interface DesktopBridge {
  connectThisMachine: (options: ConnectOptions) => Promise<void>
  cancelConnect: () => Promise<void>
  thisDevice: () => Promise<string | null>
  claudeLogin: (console: boolean, onStep: (step: ClaudeLoginStep) => void) => Promise<void>
  cancelClaudeLogin: () => Promise<void>
  claudeLogout: () => Promise<void>
  /** Points this computer's Claude Code at another model service; null in an app
   *  that predates it. */
  claudeModelService:
    | ((
        service: { baseUrl: string; token: string; model: string },
        onStep: (step: ClaudeLoginStep) => void
      ) => Promise<void>)
    | null
  disconnectThisMachine: () => Promise<void>
}

function failure(err: unknown): ConnectFailure {
  if (err && typeof err === 'object' && 'step' in err) return err as ConnectFailure
  return { step: 'prepare', detail: err instanceof Error ? err.message : String(err) }
}

/** The app's commands for this computer; null in a browser, or in an app that
 *  predates connecting step by step (it updates itself within hours). */
export function desktopBridge(): DesktopBridge | null {
  const tauri = (window as unknown as { __TAURI__?: TauriGlobal }).__TAURI__
  if (!tauri?.core || !desktopCan('device')) return null
  const { invoke, Channel } = tauri.core
  return {
    async connectThisMachine({ knownDeviceIds, approve, onStep, onPercent }) {
      let approveError: ConnectFailure | null = null
      const progress = new Channel<Progress>()
      progress.onmessage = (message) => {
        if (message.kind === 'step')
          return message.id === 'preparing' || message.id === 'browser' ? undefined : onStep(message.id)
        if (message.kind === 'percent') return onPercent(message.value)
        approve(message.text).catch((err: unknown) => {
          approveError = { step: 'approve', detail: err instanceof Error ? err.message : String(err) }
          void invoke('cancel_connect')
        })
      }
      try {
        await invoke('connect_this_machine', { knownDeviceIds, progress })
      } catch (err) {
        throw approveError ?? failure(err)
      }
    },
    async cancelConnect() {
      await invoke('cancel_connect')
    },
    async thisDevice() {
      return (await invoke('this_device')) as string | null
    },
    async claudeLogin(console, onStep) {
      const progress = new Channel<Progress>()
      progress.onmessage = (message) => {
        if (message.kind === 'step' && (message.id === 'preparing' || message.id === 'browser')) onStep(message.id)
      }
      try {
        await invoke('claude_login', { console, progress })
      } catch (err) {
        throw failure(err)
      }
    },
    async cancelClaudeLogin() {
      await invoke('cancel_claude_login')
    },
    async claudeLogout() {
      await invoke('claude_logout')
    },
    claudeModelService: desktopCan('modelService')
      ? async ({ baseUrl, token, model }, onStep) => {
          const progress = new Channel<Progress>()
          progress.onmessage = (message) => {
            if (message.kind === 'step' && message.id === 'preparing') onStep(message.id)
          }
          try {
            await invoke('claude_model_service', { baseUrl, token, model, progress })
          } catch (err) {
            throw failure(err)
          }
        }
      : null,
    async disconnectThisMachine() {
      await invoke('disconnect_this_machine')
    },
  }
}

async function myDevices() {
  try {
    return (await listMyDevices()).devices
  } catch (e) {
    // The connector answers a user with no device yet as if nobody were signed in.
    if (e instanceof ApiError && e.status === 401) return []
    throw e
  }
}

/** Connecting this computer, as the dialog shows it: asked, under way, done
 *  or stopped. One at a time, from the sign-in offer or the settings page. */
export const deviceFlow = reactive({
  open: false,
  stage: 'ask' as 'ask' | 'progress' | 'done' | 'failed',
  steps: [] as ConnectStep[],
  current: null as ConnectStep | null,
  percent: null as number | null,
  failure: null as ConnectFailure | null,
  deviceId: null as string | null,
})

const CORE_STEPS: ConnectStep[] = ['download', 'approve', 'start']
const ORDER: ConnectStep[] = ['removeOld', 'tools', 'download', 'approve', 'runtime', 'start']

// A reconnect at launch runs out of sight, until a step needs the person: the
// password for removing an old connector, or Apple's tools to install.
let quiet = false

function enter(step: ConnectStep) {
  if (quiet && (step === 'removeOld' || step === 'tools')) deviceFlow.open = true
  if (!deviceFlow.steps.includes(step)) {
    deviceFlow.steps = ORDER.filter((s) => s === step || deviceFlow.steps.includes(s))
  }
  deviceFlow.current = step
  deviceFlow.percent = null
}

async function untilOnline(deviceId: string | null): Promise<boolean> {
  // The service dials in a moment after it starts; Windows fetches its runtime first.
  for (let i = 0; i < 40; i++) {
    const devices = await myDevices().catch(() => [])
    if (devices.some((d) => d.device_id === deviceId && d.online)) return true
    await new Promise((r) => setTimeout(r, 1500))
  }
  return false
}

/** Connects this computer, showing each step in the dialog; `quiet`, for a
 *  computer connected before, shows the dialog only when a step needs the
 *  person, and closes it again once connected. Asked while one is under way,
 *  it shows that one. */
export async function startConnecting(options: { quiet?: boolean } = {}) {
  const bridge = desktopBridge()
  if (!bridge) return
  if (deviceFlow.stage === 'progress') {
    // Asked for while a reconnect runs out of sight: show it, through to the end.
    if (!options.quiet) {
      deviceFlow.open = true
      quiet = false
    }
    return
  }
  quiet = !!options.quiet
  Object.assign(deviceFlow, { open: !quiet, stage: 'progress', failure: null, deviceId: null, percent: null })
  deviceFlow.steps = [...CORE_STEPS]
  deviceFlow.current = null
  try {
    await bridge.connectThisMachine({
      knownDeviceIds: (await myDevices()).map((d) => d.device_id),
      onStep: enter,
      onPercent: (value) => (deviceFlow.percent = value),
      approve: async (code) => {
        const { device_name } = await deviceProposedName(code)
        await connectDevice(code, device_name ?? undefined)
      },
    })
    enter('start')
    deviceFlow.deviceId = await bridge.thisDevice()
    if (!(await untilOnline(deviceFlow.deviceId))) {
      stop({ step: 'start', detail: '' })
      return
    }
    deviceFlow.current = null
    if (quiet) {
      deviceFlow.open = false
      deviceFlow.stage = 'ask'
    } else deviceFlow.stage = 'done'
  } catch (err) {
    stop(failure(err))
  } finally {
    quiet = false
  }
}

// Stopped: a cancel closes the dialog; a failure shows in it, unless nobody
// was ever shown a dialog (a reconnect at launch that failed stays quiet).
function stop(why: ConnectFailure) {
  if (why.step === 'cancelled' || (quiet && !deviceFlow.open)) {
    deviceFlow.open = false
    deviceFlow.stage = 'ask'
    return
  }
  deviceFlow.failure = why
  deviceFlow.stage = 'failed'
}

export async function cancelConnecting() {
  await desktopBridge()?.cancelConnect()
}

// Whether this person was already asked to connect this computer: asked once,
// and from then on it is the settings page's to change.
const askedKey = (userId: number) => `cheese.desktop.askedToConnect.${userId}`

export function markAsked(userId: number) {
  try {
    localStorage.setItem(askedKey(userId), '1')
  } catch {
    // Storage unavailable: the question may come again at the next launch.
  }
}

function asked(userId: number) {
  try {
    return localStorage.getItem(askedKey(userId)) === '1'
  } catch {
    return false
  }
}

/** At sign-in: a computer connected before and now away comes back on its
 *  own; one never connected is asked about, once. */
export async function offerToConnect(userId: number) {
  const bridge = desktopBridge()
  if (!bridge) return
  const [stored, devices] = await Promise.all([bridge.thisDevice(), myDevices().catch(() => null)])
  if (devices === null) return
  const mine = stored ? devices.find((d) => d.device_id === stored) : undefined
  if (mine?.online) return
  if (mine) {
    // Connected before: bring it back without asking.
    await startConnecting({ quiet: true })
    return
  }
  if (asked(userId)) return
  Object.assign(deviceFlow, { open: true, stage: 'ask', failure: null, steps: [], current: null, percent: null })
}
