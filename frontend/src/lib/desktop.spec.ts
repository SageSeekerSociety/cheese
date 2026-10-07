import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listMyDevices = vi.fn()
const connectDevice = vi.fn()
vi.mock('../api', () => ({
  listMyDevices: () => listMyDevices(),
  deviceProposedName: async () => ({ device_name: 'andy-mbp' }),
  connectDevice: (...args: unknown[]) => connectDevice(...args),
}))

import { deviceFlow, markAsked, offerToConnect, startConnecting } from './desktop'

type Message = { kind: string; [k: string]: unknown }

// The desktop app's side: which device this computer already is, and a
// connection that reports its steps, hands over its code, and finishes once
// that code is approved — or stops the way `outcome` says.
function desktopHost(thisDevice: string | null, outcome: 'ok' | { step: string; detail: string } = 'ok') {
  const invoke = vi.fn(async (cmd: string, args?: Record<string, unknown>): Promise<unknown> => {
    if (cmd === 'this_device') return thisDevice
    if (cmd !== 'connect_this_machine') return
    const progress = args!.progress as { onmessage: (m: Message) => void }
    progress.onmessage({ kind: 'step', id: 'download' })
    progress.onmessage({ kind: 'percent', value: 40 })
    progress.onmessage({ kind: 'step', id: 'approve' })
    progress.onmessage({ kind: 'code', text: 'c0de' })
    await vi.waitFor(() => expect(connectDevice).toHaveBeenCalled())
    if (outcome !== 'ok') throw outcome
  })
  class Channel {
    onmessage: (m: Message) => void = () => {}
  }
  const w = window as unknown as { __TAURI__?: unknown; __CHEESE_APP__?: unknown }
  w.__TAURI__ = { core: { invoke, Channel } }
  w.__CHEESE_APP__ = { can: ['device'] }
  return invoke
}

beforeEach(() => {
  listMyDevices.mockReset().mockResolvedValue({ devices: [] })
  connectDevice.mockReset().mockResolvedValue({})
  Object.assign(deviceFlow, { open: false, stage: 'ask', steps: [], current: null, percent: null, failure: null })
})
afterEach(() => {
  const w = window as unknown as { __TAURI__?: unknown; __CHEESE_APP__?: unknown }
  delete w.__TAURI__
  delete w.__CHEESE_APP__
  localStorage.clear()
})

describe('signing in to the desktop app', () => {
  it('asks about a computer never connected, and connects nothing by itself', async () => {
    const invoke = desktopHost(null)
    await offerToConnect(10)
    expect(deviceFlow.open).toBe(true)
    expect(deviceFlow.stage).toBe('ask')
    expect(invoke).not.toHaveBeenCalledWith('connect_this_machine', expect.anything())
  })

  it('asks only once', async () => {
    desktopHost(null)
    markAsked(10)
    await offerToConnect(10)
    expect(deviceFlow.open).toBe(false)
  })

  it('leaves a computer that is already one of your online devices alone', async () => {
    listMyDevices.mockResolvedValue({ devices: [{ device_id: 'd1', online: true }] })
    const invoke = desktopHost('d1')
    await offerToConnect(10)
    expect(invoke).not.toHaveBeenCalledWith('connect_this_machine', expect.anything())
    expect(deviceFlow.open).toBe(false)
  })

  it('brings back a computer connected before, without asking', async () => {
    listMyDevices.mockResolvedValue({ devices: [{ device_id: 'd1', online: false }] })
    const invoke = desktopHost('d1')
    await offerToConnect(10)
    expect(invoke).toHaveBeenCalledWith('connect_this_machine', expect.objectContaining({ knownDeviceIds: ['d1'] }))
    expect(deviceFlow.open).toBe(false)
  })

  it('does nothing in a browser', async () => {
    await offerToConnect(10)
    expect(listMyDevices).not.toHaveBeenCalled()
    expect(deviceFlow.open).toBe(false)
  })
})

describe('connecting this computer', () => {
  it('approves it under the name it proposes, and is done once it is online', async () => {
    desktopHost('d9')
    listMyDevices
      .mockResolvedValueOnce({ devices: [] })
      .mockResolvedValue({ devices: [{ device_id: 'd9', online: true }] })
    await startConnecting()
    expect(connectDevice).toHaveBeenCalledWith('c0de', 'andy-mbp')
    expect(deviceFlow.stage).toBe('done')
    expect(deviceFlow.deviceId).toBe('d9')
  })

  it('closes without a failure when it is cancelled', async () => {
    desktopHost(null, { step: 'cancelled', detail: '' })
    await startConnecting()
    expect(deviceFlow.open).toBe(false)
    expect(deviceFlow.failure).toBeNull()
  })

  it('says at which step it stopped', async () => {
    desktopHost(null, { step: 'download', detail: 'curl: (56) Recv failure' })
    await startConnecting()
    expect(deviceFlow.stage).toBe('failed')
    expect(deviceFlow.failure).toEqual({ step: 'download', detail: 'curl: (56) Recv failure' })
  })

  it('stops the login and says why when the approval is refused', async () => {
    connectDevice.mockRejectedValue(new Error('code expired'))
    let cancelled!: () => void
    const stopped = new Promise<void>((r) => (cancelled = r))
    const invoke = desktopHost(null)
    invoke.mockImplementation(async (cmd: string, args?: Record<string, unknown>) => {
      if (cmd === 'cancel_connect') return cancelled()
      if (cmd === 'this_device') return null
      const progress = args!.progress as { onmessage: (m: Message) => void }
      progress.onmessage({ kind: 'code', text: 'c0de' })
      await stopped
      throw { step: 'cancelled', detail: '' }
    })
    await startConnecting()
    expect(invoke).toHaveBeenCalledWith('cancel_connect')
    expect(deviceFlow.stage).toBe('failed')
    expect(deviceFlow.failure).toEqual({ step: 'approve', detail: 'code expired' })
  })
})
