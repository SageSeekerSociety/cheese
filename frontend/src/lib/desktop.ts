// The desktop app (desktop/) loads this same frontend and adds one native
// ability: connecting the computer it runs on as a device. This is the page's side
// of that bridge; outside the app desktopBridge() is null and the page offers
// the download instead.

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
  }
}
