import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listAccessibleDevices = vi.fn()
const connectDevice = vi.fn()
vi.mock('../api', () => ({
  listAccessibleDevices: () => listAccessibleDevices(),
  listMyTeams: async () => [{ id: 7, handle: 'personal', name: 'Personal', personal: true }],
  deviceProposedName: async () => ({ device_name: 'andy-mbp' }),
  connectDevice: (...args: unknown[]) => connectDevice(...args),
}))

import { desktopBridge, deviceFlow, markDeclinedToConnect, offerToConnect, startConnecting } from './desktop'

type Message = { kind: string; [k: string]: unknown }

// The desktop app's side: which device this computer already is, and a
// connection that reports its steps, hands over its code, and finishes once
// that code is approved — or stops the way `outcome` says.
function desktopHost(
  thisDevice: string | null,
  outcome: 'ok' | { step: string; detail: string } = 'ok',
  /** The device id this computer has once the connection completes. */
  becomes: string | null = null,
  /** Enrollments on other profiles: the default profile's id is `thisDevice`. */
  alsoEnrolled: string[] = []
) {
  let current = thisDevice
  const invoke = vi.fn(async (cmd: string, args?: Record<string, unknown>): Promise<unknown> => {
    if (cmd === 'this_device') return current
    if (cmd === 'this_devices') return [...(current ? [current] : []), ...alsoEnrolled]
    if (cmd !== 'connect_this_machine') return
    const progress = args!.progress as { onmessage: (m: Message) => void }
    progress.onmessage({ kind: 'step', id: 'download' })
    progress.onmessage({ kind: 'percent', value: 40 })
    progress.onmessage({ kind: 'step', id: 'approve' })
    progress.onmessage({ kind: 'code', text: 'c0de' })
    await vi.waitFor(() => expect(connectDevice).toHaveBeenCalled())
    current = becomes ?? current
    if (outcome !== 'ok') throw outcome
  })
  class Channel {
    onmessage: (m: Message) => void = () => {}
  }
  const w = window as unknown as { __TAURI__?: unknown; __CHEESE_APP__?: unknown }
  w.__TAURI__ = { core: { invoke, Channel } }
  w.__CHEESE_APP__ = { can: ['device', 'teamEnrollment'] }
  return invoke
}

beforeEach(() => {
  listAccessibleDevices.mockReset().mockResolvedValue({ devices: [] })
  connectDevice.mockReset().mockResolvedValue({})
  Object.assign(deviceFlow, { open: false, stage: 'ask', steps: [], current: null, percent: null, failure: null })
})
afterEach(() => {
  const w = window as unknown as { __TAURI__?: unknown; __CHEESE_APP__?: unknown }
  delete w.__TAURI__
  delete w.__CHEESE_APP__
  localStorage.clear()
})

it('keeps Claude operations on the selected enrollment', async () => {
  const invoke = desktopHost('device')
  const bridge = desktopBridge()!
  await bridge.claudeLogin('default', () => {}, 'research')
  await bridge.claudeLogout('default', 'research')
  for (const command of ['claude_login', 'claude_logout']) {
    expect(invoke).toHaveBeenCalledWith(command, expect.objectContaining({ profile: 'research', loginId: 'default' }))
  }
})

describe('signing in to the desktop app', () => {
  it('connects a computer never connected on its own, without asking which team', async () => {
    const invoke = desktopHost(null, 'ok', 'd9')
    listAccessibleDevices.mockResolvedValue({ devices: [{ device_id: 'd9', team_id: 7, online: true }] })
    await offerToConnect(10)
    expect(invoke).toHaveBeenCalledWith('connect_this_machine', expect.objectContaining({ teamId: 7 }))
    expect(connectDevice).toHaveBeenCalledWith('c0de', 'andy-mbp', 7)
    expect(deviceFlow.stage).toBe('done')
  })

  it('does not connect a computer the person answered 暂不接入 about', async () => {
    const invoke = desktopHost(null)
    markDeclinedToConnect(10)
    await offerToConnect(10)
    expect(invoke).not.toHaveBeenCalledWith('connect_this_machine', expect.anything())
    expect(deviceFlow.open).toBe(false)
  })

  it('does not enroll again a computer removed from its team', async () => {
    const invoke = desktopHost('d1')
    await offerToConnect(10)
    expect(invoke).not.toHaveBeenCalledWith('connect_this_machine', expect.anything())
  })

  it('leaves a computer already enrolled under a team profile alone', async () => {
    // The app files its first enrollment under the team's profile, so the
    // default profile holds no id even though this computer is one of yours.
    listAccessibleDevices.mockResolvedValue({ devices: [{ device_id: 'd9', team_id: 7, online: true }] })
    const invoke = desktopHost(null, 'ok', null, ['d9'])
    await offerToConnect(10)
    expect(invoke).not.toHaveBeenCalledWith('connect_this_machine', expect.anything())
  })

  it('does not start again after the person cancelled the first connection', async () => {
    desktopHost(null, { step: 'cancelled', detail: '' })
    await offerToConnect(10)
    const invoke = desktopHost(null)
    await offerToConnect(10)
    expect(invoke).not.toHaveBeenCalledWith('connect_this_machine', expect.anything())
  })

  it('starts again after the first connection failed, not only after a cancel', async () => {
    desktopHost(null, { step: 'download', detail: 'curl: (56) Recv failure' })
    await offerToConnect(10)
    // The second launch: the machine is reachable this time, so the attempt runs
    // through to the end rather than waiting for a device that never appears.
    listAccessibleDevices.mockResolvedValue({ devices: [{ device_id: 'd9', team_id: 7, online: true }] })
    const invoke = desktopHost(null, 'ok', 'd9')
    await offerToConnect(10)
    expect(invoke).toHaveBeenCalledWith('connect_this_machine', expect.anything())
  })

  it('leaves a computer that is already one of your online devices alone', async () => {
    listAccessibleDevices.mockResolvedValue({ devices: [{ device_id: 'd1', team_id: 7, online: true }] })
    const invoke = desktopHost('d1')
    await offerToConnect(10)
    expect(invoke).not.toHaveBeenCalledWith('connect_this_machine', expect.anything())
    expect(deviceFlow.open).toBe(false)
  })

  it('brings back the enrollment that is away while another one is up', async () => {
    // A computer serves several teams, each its own connector: one of them
    // being online says nothing about the other.
    const up = { device_id: 'd1', team_id: 7, online: true }
    const away = { device_id: 'd2', team_id: 8, online: false }
    listAccessibleDevices
      .mockResolvedValueOnce({ devices: [up, away] })
      .mockResolvedValueOnce({ devices: [up, away] })
      .mockResolvedValue({ devices: [up, { ...away, online: true }] })
    const invoke = desktopHost('d1', 'ok', null, ['d2'])
    await offerToConnect(10)
    expect(invoke).toHaveBeenCalledWith('connect_this_machine', expect.objectContaining({ teamId: 8 }))
  })

  it('brings back a computer connected before, without asking', async () => {
    const away = { devices: [{ device_id: 'd1', team_id: 7, online: false }] }
    listAccessibleDevices
      .mockResolvedValueOnce(away)
      .mockResolvedValueOnce(away)
      .mockResolvedValue({ devices: [{ device_id: 'd1', team_id: 7, online: true }] })
    const invoke = desktopHost('d1')
    await offerToConnect(10)
    expect(invoke).toHaveBeenCalledWith('connect_this_machine', expect.objectContaining({ knownDeviceIds: ['d1'] }))
    expect(deviceFlow.open).toBe(false)
  })

  it('does nothing in a browser', async () => {
    await offerToConnect(10)
    expect(listAccessibleDevices).not.toHaveBeenCalled()
    expect(deviceFlow.open).toBe(false)
  })
})

describe('bringing back a computer connected before', () => {
  it('shows the dialog only when a step needs the person', async () => {
    listAccessibleDevices.mockResolvedValue({ devices: [{ device_id: 'd1', team_id: 7, online: false }] })
    const invoke = desktopHost('d1')
    invoke.mockImplementation(async (cmd: string, args?: Record<string, unknown>): Promise<unknown> => {
      if (cmd === 'this_device') return 'd1'
      if (cmd === 'this_devices') return ['d1']
      if (cmd !== 'connect_this_machine') return
      const progress = args!.progress as { onmessage: (m: Message) => void }
      progress.onmessage({ kind: 'step', id: 'removeOld' })
      throw { step: 'removeOld', detail: 'User canceled.' }
    })
    await offerToConnect(10)
    expect(deviceFlow.open).toBe(true)
    expect(deviceFlow.stage).toBe('failed')
  })

  it('stays out of sight when it fails without needing anyone', async () => {
    listAccessibleDevices.mockResolvedValue({ devices: [{ device_id: 'd1', team_id: 7, online: false }] })
    desktopHost('d1', { step: 'download', detail: 'offline' })
    await offerToConnect(10)
    expect(deviceFlow.open).toBe(false)
  })
})

describe('connecting this computer', () => {
  it('approves it under the name it proposes, and is done once it is online', async () => {
    desktopHost('d9')
    listAccessibleDevices
      .mockResolvedValueOnce({ devices: [] })
      .mockResolvedValue({ devices: [{ device_id: 'd9', team_id: 7, online: true }] })
    await startConnecting()
    expect(connectDevice).toHaveBeenCalledWith('c0de', 'andy-mbp', 7)
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
      if (cmd === 'this_devices') return []
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
