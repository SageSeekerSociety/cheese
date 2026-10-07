// 房间这一项：还没开工的 AI 队友开工时用哪台。开工前后都改得动。
import type { ComputeChoice, TopicComputeProfile } from '../types/compute'

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
    const list = screen.getAllByRole('button').filter((b) => /自有设备|独立环境/.test(b.textContent ?? ''))
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
    expect(screen.getByText(/频道里所有 AI 队友共用这一个环境.*推没推上去都会更换/)).toBeTruthy()
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
  it('shows a refused switch and offers no way around it', async () => {
    setTopicComputeChoice.mockRejectedValueOnce(new ApiError(409, '正在准备环境，稍后再换', 'ConflictError'))
    const { emitted } = mountPicker(profile({ project_default: lab }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    await fireEvent.click(screen.getByRole('button', { name: /实验室工作站/ }))
    expect((await screen.findByRole('alert')).textContent).toContain('正在准备环境')
    expect(setTopicComputeChoice).toHaveBeenLastCalledWith('topic-1', lab, {})
    expect(screen.queryByRole('button', { name: /不推送/ })).toBeNull()
    expect(emitted().changed).toBeUndefined()
  })
})

describe('what the room sees of its machine', () => {
  const onLab = (device: Partial<TopicComputeProfile['devices'][number]>, effective: 'host' | 'isolated') =>
    profile({
      current: 'device',
      choice: lab,
      device_id: 'office',
      devices: [{ device_id: 'office', name: '办公室 Mac mini', online: true, owned: false, ...device }],
      visibility: { options: [], effective, machine_access: effective === 'host' },
    })

  it('lets the machine owner give the room the whole machine', async () => {
    setTopicComputeChoice.mockResolvedValue({ choice: lab, proposal: null })
    const { emitted } = mountPicker(onLab({ owned: true, sandbox_unavailable: null }, 'isolated'))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.getByText('这个频道在 办公室 Mac mini 上能看到什么')).toBeTruthy()
    await fireEvent.click(screen.getByTestId('room-machine-host'))
    await waitFor(() => expect(setTopicComputeChoice).toHaveBeenCalledWith('topic-1', lab, { visibility: 'host' }))
    expect(emitted().changed).toHaveLength(1)
  })
  it('keeps the whole machine out of reach for anyone but its owner', async () => {
    mountPicker(onLab({ owned: false, sandbox_unavailable: null }, 'isolated'))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    const host = screen.getByTestId('room-machine-host') as HTMLButtonElement
    expect(host.disabled).toBe(true)
    expect(host.textContent).toContain('只有这台电脑的主人能开启')
  })
  it('says why a machine has no isolated environment, in the reader language', async () => {
    setLocale('en')
    mountPicker(onLab({ owned: true, sandbox_unavailable: { key: 'sandboxUnavailableWindows' } }, 'isolated'))
    await fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    const isolated = screen.getByTestId('room-machine-isolated') as HTMLButtonElement
    expect(isolated.disabled).toBe(true)
    expect(isolated.textContent).toContain('which Windows computers do not have')
    expect((screen.getByTestId('room-machine-host') as HTMLButtonElement).disabled).toBe(false)
  })
  it('names the machine an automatic room is on when access is chosen for it', async () => {
    setTopicComputeChoice.mockResolvedValue({ choice: lab, proposal: null })
    const automatic: ComputeChoice = { ...lab, name: null, device_id: null }
    mountPicker({
      ...onLab({ owned: true, sandbox_unavailable: null }, 'host'),
      choice: automatic,
      device_id: 'office',
    })
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    await fireEvent.click(screen.getByTestId('room-machine-isolated'))
    await waitFor(() =>
      expect(setTopicComputeChoice).toHaveBeenCalledWith(
        'topic-1',
        { ...automatic, name: '办公室 Mac mini', device_id: 'office' },
        { visibility: 'isolated' }
      )
    )
  })
  it('shows no access choice for a room on cloud', async () => {
    mountPicker(profile())
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.queryByTestId('room-machine-host')).toBeNull()
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
    expect(await screen.findByRole('option', { name: '云端环境' })).toBeTruthy()
    expect(screen.queryByRole('option', { name: '整台云虚拟机' })).toBeNull()
  })

  it('names a room on a whole cloud VM as such, not as a sandbox', async () => {
    const vm: ComputeChoice = { ...cloud, whole_machine: true }
    mountPicker(profile({ choice: vm, project_default: cloud, cloud_vm_available: true }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    const current = screen.getByRole('button', { name: /整台云虚拟机/ })
    expect(current.textContent).toContain('每个会话一台独立的云虚拟机')
    expect(screen.getByRole('button', { name: /云端环境/ })).not.toBe(current)
  })
})
