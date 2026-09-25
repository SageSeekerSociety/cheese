// The desktop app (desktop/) loads this same frontend and adds one native
// ability: connecting the computer it runs on as a device. This is the page's side
// of that bridge; outside the app desktopBridge() is null and the page offers
// the download instead.
import { reactive } from 'vue'
import { toast } from 'vuetify-sonner'

import { connectDevice, deviceProposedName, listMyDevices } from '../api'

// Where the desktop build is published (.github/workflows/desktop.yml).
const RELEASE = 'https://github.com/SageSeekerSociety/cheese/releases/download/desktop-latest'
export const DOWNLOADS = [
  { os: 'mac', label: 'Mac（Apple 芯片）', href: `${RELEASE}/Cheese-arm64.dmg` },
  { os: 'mac', label: 'Mac（Intel 芯片）', href: `${RELEASE}/Cheese-x64.dmg` },
  { os: 'windows', label: 'Windows', href: `${RELEASE}/Cheese-Setup-x64.exe` },
] as const

// The visitor's own system goes first; a browser does not say which Mac chip it runs on.
export function downloadsForThisComputer() {
  const own = /Windows/.test(navigator.userAgent) ? 'windows' : 'mac'
  return [...DOWNLOADS.filter((d) => d.os === own), ...DOWNLOADS.filter((d) => d.os !== own)]
}

type Progress = { kind: 'step'; text: string } | { kind: 'code'; text: string }

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
  // The devices the signed-in user owns; a stored credential for any other is dropped.
  knownDeviceIds: string[]
  // Approves the login with this page's session (POST /connector/connect).
  approve: (code: string) => Promise<void>
  onStep: (text: string) => void
}

export interface DesktopBridge {
  // Resolves once the connector is installed and running; rejects with a message to show.
  connectThisMachine: (options: ConnectOptions) => Promise<void>
  // The device this computer is logged in as, if any.
  thisDevice: () => Promise<string | null>
}

export function desktopBridge(): DesktopBridge | null {
  const tauri = (window as unknown as { __TAURI__?: TauriGlobal }).__TAURI__
  if (!tauri?.core) return null
  const { invoke, Channel } = tauri.core
  return {
    async connectThisMachine({ knownDeviceIds, approve, onStep }) {
      let approveError: Error | null = null
      const progress = new Channel<Progress>()
      progress.onmessage = (message) => {
        if (message.kind === 'step') return onStep(message.text)
        approve(message.text).catch((err: unknown) => {
          approveError = new Error(`批准失败：${err instanceof Error ? err.message : String(err)}`)
          void invoke('cancel_connect')
        })
      }
      try {
        await invoke('connect_this_machine', { knownDeviceIds, progress })
      } catch (err) {
        // A failed approval cancels the login, which reports only "已取消".
        throw approveError ?? new Error(String(err))
      }
    },
    thisDevice: async () => ((await invoke('this_device')) as string | null) ?? null,
  }
}

// One connection at a time, whoever started it: the sign-in that connects this
// computer on its own, or the button on 「我的设备」. Both show this state.
export const thisComputer = reactive({
  connecting: false,
  step: '',
  error: null as string | null,
})

async function myDevices() {
  try {
    return (await listMyDevices()).devices
  } catch (e) {
    // The connector answers a user with no device yet as if nobody were signed in.
    if (e instanceof Error && e.message.includes('requires a logged-in user')) return []
    throw e
  }
}

export async function connectThisComputer(): Promise<boolean> {
  const bridge = desktopBridge()
  if (!bridge || thisComputer.connecting) return false
  thisComputer.connecting = true
  thisComputer.error = null
  thisComputer.step = '正在准备'
  try {
    await bridge.connectThisMachine({
      knownDeviceIds: (await myDevices()).map((d) => d.device_id),
      onStep: (text) => (thisComputer.step = text),
      approve: async (code) => {
        const { device_name } = await deviceProposedName(code)
        await connectDevice(code, device_name ?? undefined)
      },
    })
    return true
  } catch (e) {
    thisComputer.error = e instanceof Error ? e.message : String(e)
    return false
  } finally {
    thisComputer.connecting = false
  }
}

// Unbinding this computer from inside the app is a no to connecting it, kept
// per account so the next sign-in does not undo it; the button says yes again.
const declinedKey = (userId: number) => `cheese.desktop.noAutoConnect.${userId}`

export function setAutoConnect(userId: number, on: boolean) {
  try {
    if (on) localStorage.removeItem(declinedKey(userId))
    else localStorage.setItem(declinedKey(userId), '1')
  } catch {
    // Storage unavailable: the choice lasts until the app restarts.
  }
}

function autoConnectDeclined(userId: number) {
  try {
    return localStorage.getItem(declinedKey(userId)) === '1'
  } catch {
    return false
  }
}

// Signed in to the desktop app means this computer is one of your devices, as
// in Claude's desktop app: nothing to find or press. Runs on every sign-in and
// launch; costs one look at the device list when the computer is already online.
export async function autoConnectThisComputer(userId: number) {
  const bridge = desktopBridge()
  if (!bridge || autoConnectDeclined(userId)) return
  const [stored, devices] = await Promise.all([bridge.thisDevice(), myDevices().catch(() => null)])
  if (devices === null) return
  if (stored && devices.some((d) => d.device_id === stored && d.online)) return
  toast('正在把这台电脑接入 Cheese')
  if (await connectThisComputer()) toast.success('这台电脑已接入，可以在「我的设备」里看到它')
  else toast.error(`这台电脑没能接入：${thisComputer.error}`)
}

// Whether a device in the list is the computer this app runs on.
export async function isThisComputer(deviceId: string) {
  return (await desktopBridge()?.thisDevice()) === deviceId
}
