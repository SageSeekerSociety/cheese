import type { ComputeChoice, TopicComputeProfile } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getTopicComputeProfile = vi.fn()
const setTopicComputeChoice = vi.fn()

vi.mock('../api', () => ({
  getTopicComputeProfile: (...args: unknown[]) => getTopicComputeProfile(...args),
  setTopicComputeChoice: (...args: unknown[]) => setTopicComputeChoice(...args),
}))

import TopicComputePicker from './TopicComputePicker.vue'

const cloud: ComputeChoice = {
  name: '云端 · 标准配置',
  profile: 'cloud',
  device_id: null,
  cores: null,
  memory_mb: null,
  disk_gb: null,
}
const lab: ComputeChoice = { ...cloud, name: '实验室工作站', profile: 'device', device_id: 'office' }

function profile(overrides: Partial<TopicComputeProfile> = {}): TopicComputeProfile {
  return {
    choice: cloud,
    project_default: cloud,
    favorites: [],
    current: 'cloud',
    device_id: null,
    devices: [
      { device_id: 'office', name: '办公室 Mac mini', online: true },
      { device_id: 'home', name: '家里那台', online: false },
    ],
    locked: false,
    inherited: false,
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
  setTopicComputeChoice.mockReset()
})

afterEach(() => cleanup())

describe('room compute choices', () => {
  it('keeps the default first without listing every team device', async () => {
    getTopicComputeProfile.mockResolvedValue(profile({ project_default: lab, choice: cloud, favorites: [lab] }))
    mountPicker()
    await fireEvent.click(await screen.findByRole('button', { name: /云端/ }))
    const list = screen.getAllByRole('button').filter((b) => /自有设备|平台标准配置/.test(b.textContent ?? ''))
    expect(list).toHaveLength(2)
    expect(list[0].textContent).toContain('实验室工作站')
    expect(within(list[0]).getByText('项目默认')).toBeTruthy()
    expect(screen.queryByText('家里那台')).toBeNull()
    expect(screen.getByRole('button', { name: /其他配置与设备/ })).toBeTruthy()
  })
  it('selects a project favorite for this room only', async () => {
    getTopicComputeProfile.mockResolvedValue(profile({ favorites: [lab] }))
    setTopicComputeChoice.mockResolvedValue({ choice: lab })
    mountPicker()
    await fireEvent.click(await screen.findByRole('button', { name: /云端/ }))
    await fireEvent.click(screen.getByRole('button', { name: /实验室工作站/ }))
    await waitFor(() => expect(setTopicComputeChoice).toHaveBeenCalledWith('topic-1', lab))
  })
  it('keeps a named offline default visible without silently substituting cloud', async () => {
    const home = { ...lab, name: '家里那台', device_id: 'home' }
    getTopicComputeProfile.mockResolvedValue(profile({ choice: home, project_default: home }))
    mountPicker()
    await fireEvent.click(await screen.findByRole('button', { name: /家里那台/ }))
    expect(screen.getByRole('button', { name: /自有设备.*离线/ })).toBeTruthy()
    expect(setTopicComputeChoice).not.toHaveBeenCalled()
  })
  it('keeps a running room locked and preserves its machine access notice', async () => {
    getTopicComputeProfile.mockResolvedValue(
      profile({
        choice: lab,
        locked: true,
        visibility: { options: [], effective: 'host', machine_access: true, notice: '让它看到整台机器' },
      })
    )
    mountPicker()
    expect(await screen.findByText('实验室工作站')).toBeTruthy()
    expect(screen.getByText('整台机器')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
})
