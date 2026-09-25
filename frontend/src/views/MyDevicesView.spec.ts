import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listMyDevices = vi.fn()
const connectDevice = vi.fn()
vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listMyDevices: () => listMyDevices(),
  listMyTeams: async () => [],
  deviceProposedName: async () => ({ device_name: 'andy-mbp' }),
  connectDevice: (...args: unknown[]) => connectDevice(...args),
}))

import MyDevicesView from './MyDevicesView.vue'

function mount() {
  return render(MyDevicesView as unknown as Component, {
    global: { plugins: [createVuetify({ components, directives })], stubs: ['router-link'] },
  })
}

// The desktop app's side of the bridge: it reports steps, hands the page the
// login code it is waiting on, and finishes once that code has been approved.
function desktopHost(finish: Promise<void>) {
  const invoke = vi.fn(async (cmd: string, args?: Record<string, unknown>) => {
    if (cmd === 'this_device') return null
    if (cmd !== 'connect_this_machine') return
    const progress = args!.progress as { onmessage: (m: unknown) => void }
    progress.onmessage({ kind: 'step', text: '正在接入' })
    progress.onmessage({ kind: 'code', text: 'c0de' })
    await finish
  })
  class Channel {
    onmessage: (m: unknown) => void = () => {}
  }
  ;(window as unknown as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke, Channel } }
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
  localStorage.setItem('accessToken', 'signed-in')
  listMyDevices.mockReset().mockResolvedValue({ devices: [] })
  connectDevice.mockReset().mockResolvedValue({})
})
afterEach(() => {
  cleanup()
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__
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
    expect(screen.queryByText('接入这台电脑')).toBeNull()
  })

  it('in the desktop app, connects this computer and approves it as the signed-in user', async () => {
    let finish!: () => void
    const invoke = desktopHost(new Promise<void>((r) => (finish = r)))
    mount()

    await fireEvent.click(await screen.findByText('接入这台电脑'))
    await screen.findByText('正在接入')
    await vi.waitFor(() => expect(connectDevice).toHaveBeenCalledWith('c0de', 'andy-mbp'))
    expect(invoke).toHaveBeenCalledWith('connect_this_machine', expect.objectContaining({ knownDeviceIds: [] }))

    listMyDevices.mockResolvedValue({
      devices: [{ device_id: 'd1', name: 'andy-mbp', online: true, team_ids: [], screens: [] }],
    })
    finish()
    expect(await screen.findByText('andy-mbp')).toBeTruthy()
  })

  it('in the desktop app, says why when the approval is refused and stops the login', async () => {
    connectDevice.mockRejectedValue(new Error('code expired'))
    let finish!: () => void
    const invoke = desktopHost(new Promise<void>((r) => (finish = r)))
    invoke.mockImplementation(async (cmd: string, args?: Record<string, unknown>) => {
      if (cmd === 'cancel_connect') {
        finish()
        return undefined
      }
      if (cmd === 'this_device') return null
      const progress = args!.progress as { onmessage: (m: unknown) => void }
      progress.onmessage({ kind: 'code', text: 'c0de' })
      await new Promise<void>((r) => (finish = r))
      throw new Error('已取消')
    })
    mount()

    await fireEvent.click(await screen.findByText('接入这台电脑'))
    expect(await screen.findByText('批准失败：code expired')).toBeTruthy()
    expect(invoke).toHaveBeenCalledWith('cancel_connect')
  })
})
