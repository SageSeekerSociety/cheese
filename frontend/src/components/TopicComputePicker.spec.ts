// 房间这一项：还没开工的 AI 队友开工时用哪台。开工前后都改得动。
import type { ComputeChoice, TopicComputeProfile } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const setTopicComputeChoice = vi.fn()
const ApiError = vi.hoisted(
  () =>
    class extends Error {
      constructor(
        readonly status: number,
        message: string,
        readonly code?: string
      ) {
        super(message)
      }
    }
)

vi.mock('../api', () => ({
  ApiError,
  setTopicComputeChoice: (...args: unknown[]) => setTopicComputeChoice(...args),
}))

import { setLocale } from '../i18n'

import TopicComputePicker from './TopicComputePicker.vue'

const cloud: ComputeChoice = {
  name: null,
  profile: 'cloud',
  device_id: null,
}
const lab: ComputeChoice = { ...cloud, name: '实验室工作站', profile: 'device', device_id: 'office' }

function profile(overrides: Partial<TopicComputeProfile> = {}): TopicComputeProfile {
  return {
    choice: cloud,
    project_default: cloud,
    current: 'cloud',
    device_id: null,
    devices: [
      { device_id: 'office', name: '办公室 Mac mini', online: true },
      { device_id: 'home', name: '家里那台', online: false },
    ],
    sessions: [],
    cloud_vm_available: false,
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
    mountPicker(profile({ project_default: lab, choice: cloud }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    const list = screen.getAllByRole('button').filter((b) => /自有设备|独立沙箱/.test(b.textContent ?? ''))
    expect(list).toHaveLength(2)
    expect(list[0].textContent).toContain('实验室工作站')
    expect(within(list[0]).getByText('项目默认')).toBeTruthy()
    expect(screen.queryByText('家里那台')).toBeNull()
    expect(screen.getByRole('button', { name: /其他配置与设备/ })).toBeTruthy()
  })
  it('sets the machine the whole room runs on', async () => {
    setTopicComputeChoice.mockResolvedValue({ choice: lab, proposal: null })
    const { emitted } = mountPicker(profile({ project_default: lab }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.getByText(/房间里所有 AI 队友共用这一台/)).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: /实验室工作站/ }))
    await waitFor(() => expect(setTopicComputeChoice).toHaveBeenCalledWith('topic-1', lab, {}))
    expect(emitted().changed).toHaveLength(1)
  })
  it('stays changeable after the room has started, and moves the room', async () => {
    setTopicComputeChoice.mockResolvedValue({ choice: lab, proposal: null })
    const working: ComputeChoice = { ...lab, name: '办公室 Mac mini' }
    mountPicker(
      profile({
        project_default: lab,
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
    await waitFor(() => expect(setTopicComputeChoice).toHaveBeenCalledWith('topic-1', lab, {}))
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
    const { emitted } = mountPicker(profile({ project_default: lab }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    await fireEvent.click(screen.getByRole('button', { name: /实验室工作站/ }))
    expect((await screen.findByRole('status')).textContent).toContain('这一步等 @andyl 点头。')
    expect(emitted().changed).toBeUndefined()
  })
  it('offers to switch without pushing only when the old machine is unreachable', async () => {
    setTopicComputeChoice.mockRejectedValueOnce(
      new ApiError(409, '原来那台工作电脑连不上，无法推送改动，没有更换', 'WorkComputerUnreachable')
    )
    const { emitted } = mountPicker(profile({ project_default: lab }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    await fireEvent.click(screen.getByRole('button', { name: /实验室工作站/ }))
    expect((await screen.findByRole('alert')).textContent).toContain('连不上')

    setTopicComputeChoice.mockResolvedValueOnce({ choice: lab, proposal: null })
    await fireEvent.click(screen.getByTestId('room-machine-abandon'))
    await waitFor(() =>
      expect(setTopicComputeChoice).toHaveBeenLastCalledWith('topic-1', lab, { abandonUnpushed: true })
    )
    expect(emitted().changed).toHaveLength(1)
  })
})

describe('cloud sandbox choice', () => {
  it('asks for no machine size: the cloud is a sandbox, chosen as such', async () => {
    setTopicComputeChoice.mockResolvedValue({ choice: cloud, proposal: null })
    mountPicker(profile({ choice: lab, project_default: lab }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    await fireEvent.click(screen.getByRole('button', { name: /其他配置与设备/ }))
    expect(screen.queryByLabelText(/CPU|内存|磁盘/)).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))
    await waitFor(() =>
      expect(setTopicComputeChoice).toHaveBeenCalledWith(
        'topic-1',
        { name: null, profile: 'cloud', device_id: null, whole_machine: false },
        {}
      )
    )
  })
})

describe('whole cloud VM choice', () => {
  async function openForm(state: TopicComputeProfile) {
    mountPicker(state)
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    await fireEvent.click(screen.getByRole('button', { name: /其他配置与设备/ }))
    await fireEvent.mouseDown(await screen.findByRole('combobox'))
  }

  it('is offered where the deployment has one, and saved as cloud with the whole machine', async () => {
    setTopicComputeChoice.mockResolvedValue({ choice: cloud, proposal: null })
    await openForm(profile({ choice: lab, project_default: lab, cloud_vm_available: true }))
    await fireEvent.click(await screen.findByRole('option', { name: '整台云虚拟机' }))
    expect(screen.getByText(/每个会话一台独立的云虚拟机，有 sudo，能跑 Docker/)).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))
    await waitFor(() =>
      expect(setTopicComputeChoice).toHaveBeenCalledWith(
        'topic-1',
        { name: null, profile: 'cloud', device_id: null, whole_machine: true },
        {}
      )
    )
  })

  it('is not offered where the deployment has none', async () => {
    await openForm(profile({ choice: lab, project_default: lab, cloud_vm_available: false }))
    expect(await screen.findByRole('option', { name: '云端沙箱' })).toBeTruthy()
    expect(screen.queryByRole('option', { name: '整台云虚拟机' })).toBeNull()
  })

  it('names a room on a whole cloud VM as such, not as a sandbox', async () => {
    const vm: ComputeChoice = { ...cloud, whole_machine: true }
    mountPicker(profile({ choice: vm, project_default: cloud, cloud_vm_available: true }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    const current = screen.getByRole('button', { name: /整台云虚拟机/ })
    expect(current.textContent).toContain('每个会话一台独立的云虚拟机')
    expect(screen.getByRole('button', { name: /云端沙箱/ })).not.toBe(current)
  })
})
