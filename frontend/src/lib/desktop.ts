// The desktop app (desktop/) loads this same frontend and adds one native
// ability: connecting the computer it runs on as a device. This is the page's side
// of that bridge; outside the app desktopBridge() is null and the page offers
// the download instead.
import { reactive } from 'vue'

import { ApiError, connectDevice, deviceProposedName, listAccessibleDevices, listMyTeams } from '../api'

import { storageGet, storageSet } from './browserStorage'
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
  teamId: number
  approve: (code: string) => Promise<void>
  onStep: (step: ConnectStep) => void
  onPercent: (value: number) => void
}

export interface DesktopBridge {
  modelCredentialLogin: boolean
  connectThisMachine: (options: ConnectOptions) => Promise<void>
  cancelConnect: () => Promise<void>
  thisDevice: () => Promise<string | null>
  preventSleep: (enabled?: boolean) => Promise<boolean>
  thisDevices: () => Promise<string[]>
  enrollmentProfiles: () => Promise<Record<string, string>>
  /** Logs in the login this computer calls `loginId` again, or adds one when it is null. */
  claudeLogin: (
    loginId: string | null,
    onStep: (step: ClaudeLoginStep) => void,
    profile?: string,
    authorization?: { server: string; ticket: string }
  ) => Promise<void>
  cancelClaudeLogin: () => Promise<void>
  /** Logs out the login this computer calls `loginId`; its other logins stay. */
  claudeLogout: (loginId: string, profile?: string) => Promise<void>
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
  if (!tauri?.core || !desktopCan('teamEnrollment')) return null
  const { invoke, Channel } = tauri.core
  return {
    modelCredentialLogin: desktopCan('modelCredentialLogin'),
    async connectThisMachine({ knownDeviceIds, teamId, approve, onStep, onPercent }) {
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
        await invoke('connect_this_machine', { knownDeviceIds, teamId, progress })
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
    async preventSleep(enabled) {
      return (await invoke('prevent_sleep', { enabled: enabled ?? null })) as boolean
    },
    async thisDevices() {
      return (await invoke('this_devices')) as string[]
    },
    async enrollmentProfiles() {
      return (await invoke('enrollment_profiles')) as Record<string, string>
    },
    async claudeLogin(loginId, onStep, profile = 'default', authorization) {
      const progress = new Channel<Progress>()
      progress.onmessage = (message) => {
        if (message.kind === 'step' && (message.id === 'preparing' || message.id === 'browser')) onStep(message.id)
      }
      try {
        await invoke('claude_login', {
          loginId,
          progress,
          profile,
          server: authorization?.server ?? null,
          ticket: authorization?.ticket ?? null,
        })
      } catch (err) {
        throw failure(err)
      }
    },
    async cancelClaudeLogin() {
      await invoke('cancel_claude_login')
    },
    async claudeLogout(loginId, profile = 'default') {
      await invoke('claude_logout', { loginId, profile })
    },
    async disconnectThisMachine() {
      await invoke('disconnect_this_machine')
    },
  }
}

async function myDevices() {
  try {
    return (await listAccessibleDevices()).devices
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
  deviceName: '',
  teamId: null as number | null,
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

/** How a connection ended: it is this computer's device now, the person
 *  stopped it, or it did not work. */
export type ConnectOutcome = 'connected' | 'cancelled' | 'failed'

/** Connects this computer, showing each step in the dialog; `quiet`, for a
 *  computer connected before, shows the dialog only when a step needs the
 *  person, and closes it again once connected. Asked while one is under way,
 *  it shows that one and answers null. */
export async function startConnecting(
  options: { quiet?: boolean; teamId?: number } = {}
): Promise<ConnectOutcome | null> {
  const bridge = desktopBridge()
  if (!bridge) return null
  if (deviceFlow.stage === 'progress') {
    // Asked for while a reconnect runs out of sight: show it, through to the end.
    if (!options.quiet) {
      deviceFlow.open = true
      quiet = false
    }
    return null
  }
  quiet = !!options.quiet
  Object.assign(deviceFlow, { open: !quiet, stage: 'progress', failure: null, deviceId: null, percent: null })
  deviceFlow.steps = [...CORE_STEPS]
  deviceFlow.current = null
  try {
    const teams = await listMyTeams()
    const teamId = options.teamId ?? teams.find((team) => team.personal)?.id
    if (teamId === undefined) throw new Error('No team selected')
    deviceFlow.teamId = teamId
    const before = await bridge.thisDevices()
    const known = (await myDevices()).filter((device) => device.team_id === teamId)
    await bridge.connectThisMachine({
      knownDeviceIds: known.map((d) => d.device_id),
      teamId,
      onStep: enter,
      onPercent: (value) => (deviceFlow.percent = value),
      approve: async (code) => {
        const { device_name } = await deviceProposedName(code)
        await connectDevice(code, device_name ?? undefined, teamId)
      },
    })
    enter('start')
    const ids = await bridge.thisDevices()
    const after = await myDevices()
    const enrolled =
      after.find((d) => ids.includes(d.device_id) && d.team_id === teamId && !before.includes(d.device_id)) ??
      after.find((d) => ids.includes(d.device_id) && d.team_id === teamId)
    deviceFlow.deviceId = enrolled?.device_id ?? null
    deviceFlow.deviceName = enrolled?.name ?? ''
    if (!(await untilOnline(deviceFlow.deviceId))) {
      stop({ step: 'start', detail: '' })
      return 'failed'
    }
    deviceFlow.current = null
    if (quiet) {
      deviceFlow.open = false
      deviceFlow.stage = 'ask'
    } else deviceFlow.stage = 'done'
    return 'connected'
  } catch (err) {
    const why = failure(err)
    stop(why)
    return why.step === 'cancelled' ? 'cancelled' : 'failed'
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

export function requestConnecting(teamId?: number) {
  deviceFlow.teamId = teamId ?? null
  Object.assign(deviceFlow, { open: true, stage: 'ask', failure: null })
}

export async function cancelConnecting() {
  await desktopBridge()?.cancelConnect()
}

// Whether this person answered 「暂不接入」 about this computer. Connecting on
// its own at sign-in must not overrule that answer.
const declinedKey = (userId: number) => `cheese.desktop.connectDeclined.${userId}`

export function markDeclinedToConnect(userId: number) {
  // A write that does not land only means the machine may be connected at the
  // next launch — which is what happens when the answer cannot be kept.
  storageSet(declinedKey(userId), '1')
}

function declined(userId: number) {
  return storageGet(declinedKey(userId)) === '1'
}

/** At sign-in: a computer connected before and now away comes back on its
 *  own, and one never connected is connected on its own too — installing the
 *  app is meant to end with this computer serving a team, not with a question.
 *  It serves the person's own team, which every account has. The dialog shows
 *  the steps and the download's progress; it is never a question. */
export async function offerToConnect(userId: number) {
  const bridge = desktopBridge()
  if (!bridge) return
  // Every enrollment on this computer, not only the default profile: the app's
  // first connection is filed under the team's profile.
  const [ids, devices] = await Promise.all([bridge.thisDevices(), myDevices().catch(() => null)])
  if (devices === null) return
  const mine = devices.filter((device) => ids.includes(device.device_id))
  if (mine.length) {
    // Connected before: bring back each one that is away. One enrollment up
    // says nothing about another — a computer serves several teams, each its
    // own connector — so this is per device, not "is this computer online".
    for (const device of mine.filter((d) => !d.online)) {
      await startConnecting({ quiet: true, teamId: device.team_id })
    }
    return
  }
  // Enrolled here but none of them this person's: removed from its team, or
  // another account's. Do not enroll it again behind their back.
  if (ids.length) return
  if (declined(userId)) return
  // Stopping the first connection is a 「暂不接入」 like any other: the next
  // launch must not start it again. A failure is not.
  if ((await startConnecting()) === 'cancelled') markDeclinedToConnect(userId)
}
