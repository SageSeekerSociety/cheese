import type { TopicComputeProfile } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getTopicComputeProfile = vi.fn()
const setTopicComputeProfile = vi.fn()

vi.mock('../api', () => ({
  getTopicComputeProfile: (...args: unknown[]) => getTopicComputeProfile(...args),
  setTopicComputeProfile: (...args: unknown[]) => setTopicComputeProfile(...args),
}))

import TopicComputePicker from './TopicComputePicker.vue'

function profile(overrides: Partial<TopicComputeProfile> = {}): TopicComputeProfile {
  return {
    current: 'cloud',
    device_id: null,
    devices: [
      { device_id: 'office', name: '办公室 Mac mini', online: true },
      { device_id: 'home', name: '家里那台', online: false },
    ],
    locked: false,
    inherited: false,
    sticky: 'cloud',
    profiles: [
      {
        kind: 'compute',
        id: 'device',
        label: '自托管设备（我的机器）',
        tier: 'byo',
        price: '自备',
        description: '在你自己连接的机器上跑。',
        available: true,
        default: false,
      },
      {
        kind: 'compute',
        id: 'cloud',
        label: 'Cloud',
        tier: 'premium',
        price: '按量计费',
        description: '为这个话题创建一台独占云端机器。',
        available: true,
        default: true,
      },
    ],
    visibility: {
      options: [],
      effective: null,
      machine_access: false,
      notice: '让它看到整台机器',
    },
    ...overrides,
  }
}

function mountPicker() {
  return render(TopicComputePicker, {
    props: { topicId: 'topic-1' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: {
        width: 1024,
        height: 768,
        offsetLeft: 0,
        offsetTop: 0,
        addEventListener() {},
        removeEventListener() {},
      },
    })
  }
  if (!globalThis.devicePixelRatio) {
    Object.defineProperty(globalThis, 'devicePixelRatio', {
      configurable: true,
      value: 1,
    })
  }
})

beforeEach(() => {
  getTopicComputeProfile.mockReset()
  setTopicComputeProfile.mockReset()
})

afterEach(() => cleanup())

describe('topic compute machine selection', () => {
  it('shows automatic and named choices and allows an offline machine', async () => {
    getTopicComputeProfile.mockResolvedValue(profile())
    setTopicComputeProfile.mockResolvedValue({
      current: 'device',
      device_id: 'home',
      locked: false,
      inherited: false,
    })
    mountPicker()

    const activator = await screen.findByRole('button', { name: /Cloud/ })
    await fireEvent.click(activator)

    expect(await screen.findByText('系统挑一台')).toBeTruthy()
    const office = screen.getByRole('button', { name: /办公室 Mac mini/ })
    const home = screen.getByRole('button', { name: /家里那台/ })
    expect(within(office).getByText('在线')).toBeTruthy()
    expect(within(home).getByText('离线')).toBeTruthy()
    expect((home as HTMLButtonElement).disabled).toBe(false)

    await fireEvent.click(home)
    await waitFor(() => {
      expect(setTopicComputeProfile).toHaveBeenCalledWith('topic-1', 'device', 'home')
    })
  })

  it('disables automatic choice without an online machine but keeps named choices enabled', async () => {
    const state = profile({
      devices: [{ device_id: 'home', name: '家里那台', online: false }],
    })
    state.profiles[0] = { ...state.profiles[0], available: false }
    getTopicComputeProfile.mockResolvedValue(state)
    setTopicComputeProfile.mockResolvedValue({
      current: 'device',
      device_id: 'home',
      locked: false,
      inherited: false,
    })
    mountPicker()

    await fireEvent.click(await screen.findByRole('button', { name: /Cloud/ }))
    const automatic = await screen.findByRole('button', { name: /系统挑一台/ })
    const home = screen.getByRole('button', { name: /家里那台/ })
    expect((automatic as HTMLButtonElement).disabled).toBe(true)
    expect((home as HTMLButtonElement).disabled).toBe(false)

    await fireEvent.click(home)
    await waitFor(() => {
      expect(setTopicComputeProfile).toHaveBeenCalledWith('topic-1', 'device', 'home')
    })
  })

  it('keeps automatic selection as a null device choice', async () => {
    getTopicComputeProfile.mockResolvedValue(profile())
    setTopicComputeProfile.mockResolvedValue({
      current: 'device',
      device_id: null,
      locked: false,
      inherited: false,
    })
    mountPicker()

    await fireEvent.click(await screen.findByRole('button', { name: /Cloud/ }))
    await fireEvent.click(await screen.findByRole('button', { name: /系统挑一台/ }))

    await waitFor(() => {
      expect(setTopicComputeProfile).toHaveBeenCalledWith('topic-1', 'device', null)
    })
  })

  it('shows only the locked machine after the topic has run', async () => {
    getTopicComputeProfile.mockResolvedValue(
      profile({
        current: 'device',
        device_id: 'home',
        locked: true,
        visibility: {
          options: [],
          effective: 'host',
          machine_access: true,
          notice: '让它看到整台机器',
        },
      })
    )
    mountPicker()

    expect(await screen.findByText('家里那台')).toBeTruthy()
    expect(screen.queryByText('系统挑一台')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    expect(setTopicComputeProfile).not.toHaveBeenCalled()
  })
})
