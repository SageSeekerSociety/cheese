// 房间这一项：还没开工的 AI 队友开工时用哪台。开工前后都改得动。
import type { ComputeChoice, TopicComputeProfile } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const setTopicComputeChoice = vi.fn()

vi.mock('../api', () => ({
  setTopicComputeChoice: (...args: unknown[]) => setTopicComputeChoice(...args),
}))

import { setLocale } from '../i18n'

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
    sessions: [],
    profiles: [
      {
        kind: 'compute',
        id: 'device',
        label: '自有设备',
        tier: 'byo',
        price: '自备',
        description: '在你自己连接的机器上跑。',
        available: true,
        default: false,
      },
      {
        kind: 'compute',
        id: 'cloud',
        label: '云端',
        tier: 'premium',
        price: '按量计费',
        description: '为房间创建一台云端工作电脑。',
        available: true,
        default: true,
      },
    ],
    visibility: {
      options: [],
      effective: null,
      machine_access: false,
      notice: '让它看到能访问整台机器',
    },
    ...overrides,
  }
}

function mountPicker(state: TopicComputeProfile) {
  return render(TopicComputePicker, {
    props: { topicId: 'topic-1', profile: state },
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
  setLocale('zh-CN')
  setTopicComputeChoice.mockReset()
})

afterEach(() => cleanup())

describe('room work computer choice', () => {
  it('keeps the default first without listing every team device', async () => {
    mountPicker(profile({ project_default: lab, choice: cloud, favorites: [lab] }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    const list = screen.getAllByRole('button').filter((b) => /自有设备|平台标准配置/.test(b.textContent ?? ''))
    expect(list).toHaveLength(2)
    expect(list[0].textContent).toContain('实验室工作站')
    expect(within(list[0]).getByText('项目默认')).toBeTruthy()
    expect(screen.queryByText('家里那台')).toBeNull()
    expect(screen.getByRole('button', { name: /其他配置与设备/ })).toBeTruthy()
  })
  it('sets what AI teammates that have not started will use', async () => {
    setTopicComputeChoice.mockResolvedValue({ choice: lab, proposal: null })
    const { emitted } = mountPicker(profile({ favorites: [lab] }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.getByText('只影响还没开工的 AI 队友；已经在干活的继续用自己那台')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: /实验室工作站/ }))
    await waitFor(() => expect(setTopicComputeChoice).toHaveBeenCalledWith('topic-1', lab))
    expect(emitted().changed).toHaveLength(1)
  })
  it('stays changeable after the room has started', async () => {
    setTopicComputeChoice.mockResolvedValue({ choice: lab, proposal: null })
    const working: ComputeChoice = { ...lab, name: '办公室 Mac mini' }
    mountPicker(
      profile({
        favorites: [lab],
        sessions: [
          {
            id: 's1',
            agent_handle: 'analyst',
            harness: 'test-harness',
            choice: working,
            lease: null,
            machine_access: true,
          },
        ],
      })
    )
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.queryByText('房间初始配置')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: /实验室工作站/ }))
    await waitFor(() => expect(setTopicComputeChoice).toHaveBeenCalledWith('topic-1', lab))
  })
  it('keeps a named offline default visible without silently substituting cloud', async () => {
    const home = { ...lab, name: '家里那台', device_id: 'home' }
    mountPicker(profile({ choice: home, project_default: home }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.getByRole('button', { name: /家里那台.*自有设备.*离线/ })).toBeTruthy()
    expect(setTopicComputeChoice).not.toHaveBeenCalled()
  })
  it('says a choice became a proposal instead of changing silently', async () => {
    setTopicComputeChoice.mockResolvedValue({
      choice: cloud,
      proposal: { approver: 'andyl', tier: 'byo', content: '这一步等 @andyl 点头。' },
    })
    const { emitted } = mountPicker(profile({ favorites: [lab] }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    await fireEvent.click(screen.getByRole('button', { name: /实验室工作站/ }))
    expect((await screen.findByRole('status')).textContent).toContain('这一步等 @andyl 点头。')
    expect(emitted().changed).toBeUndefined()
  })
})
