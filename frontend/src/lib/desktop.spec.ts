import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listMyDevices = vi.fn()
const connectDevice = vi.fn()
vi.mock('../api', () => ({
  listMyDevices: () => listMyDevices(),
  deviceProposedName: async () => ({ device_name: 'andy-mbp' }),
  connectDevice: (...args: unknown[]) => connectDevice(...args),
}))
const { toast } = vi.hoisted(() => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('vuetify-sonner', () => ({ toast }))

import { autoConnectThisComputer, setAutoConnect } from './desktop'

// The desktop app's side: which device this computer already is, and a login
// that hands over its code and finishes once that code is approved.
function desktopHost(thisDevice: string | null) {
  const invoke = vi.fn(async (cmd: string, args?: Record<string, unknown>) => {
    if (cmd === 'this_device') return thisDevice
    if (cmd !== 'connect_this_machine') return
    const progress = args!.progress as { onmessage: (m: unknown) => void }
    progress.onmessage({ kind: 'code', text: 'c0de' })
    await vi.waitFor(() => expect(connectDevice).toHaveBeenCalled())
  })
  class Channel {
    onmessage: (m: unknown) => void = () => {}
  }
  ;(window as unknown as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke, Channel } }
  return invoke
}

beforeEach(() => {
  listMyDevices.mockReset().mockResolvedValue({ devices: [] })
  connectDevice.mockReset().mockResolvedValue({})
  toast.mockReset()
  toast.success.mockReset()
  toast.error.mockReset()
})
afterEach(() => {
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__
  localStorage.clear()
})

describe('signing in to the desktop app', () => {
  it('connects this computer without anyone pressing a button', async () => {
    const invoke = desktopHost(null)
    await autoConnectThisComputer(10)
    expect(invoke).toHaveBeenCalledWith('connect_this_machine', expect.anything())
    expect(connectDevice).toHaveBeenCalledWith('c0de', 'andy-mbp')
    expect(toast.success).toHaveBeenCalled()
  })

  it('leaves a computer that is already one of your online devices alone', async () => {
    listMyDevices.mockResolvedValue({ devices: [{ device_id: 'd1', online: true }] })
    const invoke = desktopHost('d1')
    await autoConnectThisComputer(10)
    expect(invoke).not.toHaveBeenCalledWith('connect_this_machine', expect.anything())
    expect(toast).not.toHaveBeenCalled()
  })

  it('reconnects a computer that is yours but offline', async () => {
    listMyDevices.mockResolvedValue({ devices: [{ device_id: 'd1', online: false }] })
    const invoke = desktopHost('d1')
    await autoConnectThisComputer(10)
    expect(invoke).toHaveBeenCalledWith('connect_this_machine', expect.objectContaining({ knownDeviceIds: ['d1'] }))
  })

  it('does not connect again once this account unbound this computer in the app', async () => {
    setAutoConnect(10, false)
    const invoke = desktopHost(null)
    await autoConnectThisComputer(10)
    expect(invoke).not.toHaveBeenCalled()
  })

  it('does nothing in a browser', async () => {
    await autoConnectThisComputer(10)
    expect(listMyDevices).not.toHaveBeenCalled()
  })
})
