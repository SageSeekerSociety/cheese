import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listMyDevices = vi.fn()
const connectDevice = vi.fn()
const checkSandbox = vi.fn()
vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listMyDevices: () => listMyDevices(),
  listMyTeams: async () => [],
  deviceProposedName: async () => ({ device_name: 'andy-mbp' }),
  connectDevice: (...args: unknown[]) => connectDevice(...args),
  checkSandbox: (...args: unknown[]) => checkSandbox(...args),
}))

import MyDevicesView from './MyDevicesView.vue'

import { setLocale } from '@/i18n'

function mount() {
  return render(MyDevicesView as unknown as Component, {
    global: { plugins: [createVuetify({ components, directives })], stubs: ['router-link'] },
  })
}

// The desktop app's side of the bridge: it reports steps, hands the page the
// login code it is waiting on, and finishes once that code has been approved.
function desktopHost(finish: Promise<void>) {
  const invoke = vi.fn(async (cmd: string, args?: Record<string, unknown>): Promise<unknown> => {
    if (cmd === 'this_device') return null
    if (cmd !== 'connect_this_machine') return
    const progress = args!.progress as { onmessage: (m: unknown) => void }
    progress.onmessage({ kind: 'step', id: 'approve' })
    progress.onmessage({ kind: 'code', text: 'c0de' })
    await finish
  })
  class Channel {
    onmessage: (m: unknown) => void = () => {}
  }
  const w = window as unknown as { __TAURI__?: unknown; __CHEESE_APP__?: unknown }
  w.__TAURI__ = { core: { invoke, Channel } }
  w.__CHEESE_APP__ = { can: ['device'] }
  return invoke
}

// 「添加设备」 is a VOverlay, and happy-dom has no visualViewport to place it with.
vi.stubGlobal('visualViewport', {
  width: 1024,
  height: 768,
  offsetLeft: 0,
  offsetTop: 0,
  addEventListener() {},
  removeEventListener() {},
})

beforeEach(() => {
  // The download labels come from the catalog, and happy-dom starts in English.
  setLocale('zh-CN')
  localStorage.setItem('accessToken', 'signed-in')
  listMyDevices.mockReset().mockResolvedValue({ devices: [] })
  connectDevice.mockReset().mockResolvedValue({})
})
afterEach(() => {
  cleanup()
  const w = window as unknown as { __TAURI__?: unknown; __CHEESE_APP__?: unknown }
  delete w.__TAURI__
  delete w.__CHEESE_APP__
  localStorage.clear()
})

describe('adding a device', () => {
  it('in a browser, offers the desktop app and keeps the terminal command', async () => {
    mount()
    await fireEvent.click((await screen.findAllByText('添加设备'))[0])
    expect((await screen.findByText('Mac（Apple 芯片）')).closest('a')?.getAttribute('href')).toMatch(
      /Cheese-arm64\.dmg$/
    )
    expect(screen.getByText('Windows').closest('a')?.getAttribute('href')).toMatch(/Cheese-Setup-x64\.exe$/)
    expect(screen.getAllByText(/connector\/install\.sh/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/irm .*\/connector\/install\.ps1 \| iex/).length).toBeGreaterThan(0)
    expect(screen.queryByText('接入这台设备')).toBeNull()
  })

  it('in the desktop app, connects this device as the signed-in user and lists it once online', async () => {
    let finish!: () => void
    const invoke = desktopHost(new Promise<void>((r) => (finish = r)))
    mount()

    await fireEvent.click(await screen.findByText('接入这台设备'))
    await vi.waitFor(() => expect(connectDevice).toHaveBeenCalledWith('c0de', 'andy-mbp'))
    expect(invoke).toHaveBeenCalledWith('connect_this_machine', expect.objectContaining({ knownDeviceIds: [] }))

    invoke.mockImplementation(async (cmd: string) => (cmd === 'this_device' ? 'd1' : undefined))
    listMyDevices.mockResolvedValue({
      devices: [{ device_id: 'd1', name: 'andy-mbp', online: true, team_ids: [], screens: [] }],
    })
    finish()
    expect(await screen.findByText('andy-mbp')).toBeTruthy()
  })
})

describe('the screens open on a device', () => {
  const screenOf = (sid: string, handle: string, name: string | null, source: string | null = 'human') => ({
    sid,
    agent_handle: handle,
    agent_user_id: 'u-' + sid,
    project_id: name ? 'p1' : null,
    topic_id: name ? 't1' : null,
    agent_name: name,
    agent_name_source: name ? source : null,
  })

  it('names the agent on each screen, and in the session it opens', async () => {
    listMyDevices.mockResolvedValue({
      devices: [
        {
          device_id: 'd1',
          name: 'lab-box',
          online: true,
          project_ids: [],
          team_ids: [],
          screens: [
            screenOf('s1', 'cheese-kimi', 'Kimi'),
            screenOf('s2', 'cheese', '芝士', 'default'),
            screenOf('s3', 'room-agent-1', null),
          ],
        },
      ],
    })
    render(MyDevicesView as unknown as Component, {
      global: {
        plugins: [createVuetify({ components, directives })],
        stubs: ['router-link', 'DeviceLiveViewer'],
      },
    })

    const kimi = await screen.findByText('查看现场 · @Kimi')
    expect(screen.getByText('查看现场 · @芝士')).toBeTruthy()
    // A screen in no room has no name to give; its handle stands in.
    expect(screen.getByText('查看现场 · @room-agent-1')).toBeTruthy()
    expect(screen.queryByText(/@cheese-kimi/)).toBeNull()

    await fireEvent.click(kimi)
    expect(await screen.findByText('现场 · @Kimi')).toBeTruthy()
  })

  it('tells the owner, in their language, why a machine cannot isolate a channel, as it answers again', async () => {
    const reason = { key: 'sandboxAppArmor', params: {} }
    const said = { isolates: false, reason, checked_at: '2026-10-10T00:00:00Z' }
    listMyDevices.mockResolvedValue({
      devices: [
        { device_id: 'd1', name: 'lab-box', online: true, project_ids: [], team_ids: [], screens: [], sandbox: said },
      ],
    })
    checkSandbox.mockResolvedValue({ ...said, reason: { key: 'sandboxNotPlaced', params: {} } })
    mount()

    expect(await screen.findByText(/cheesehost 还没在这台电脑上放好 bubblewrap/)).toBeTruthy()
    expect(checkSandbox).toHaveBeenCalledWith('d1')
    expect(screen.getByTestId('device-sandbox').textContent).toContain('给不了频道隔离环境')
  })

  it('says nothing of a machine that can', async () => {
    listMyDevices.mockResolvedValue({
      devices: [
        {
          device_id: 'd1',
          name: 'lab-box',
          online: true,
          project_ids: [],
          team_ids: [],
          screens: [],
          sandbox: { isolates: true, reason: null, checked_at: '2026-10-10T00:00:00Z' },
        },
      ],
    })
    checkSandbox.mockResolvedValue({ isolates: true, reason: null, checked_at: '2026-10-10T00:00:00Z' })
    mount()

    await screen.findByText('lab-box')
    expect(screen.queryByTestId('device-sandbox')).toBeNull()
  })
})
