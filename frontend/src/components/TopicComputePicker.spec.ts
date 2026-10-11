// 房间这一项：还没开工的 AI 队友开工时用哪台。开工前后都改得动。
//
// 它现在只吃 props、只往上发 `select` —— 发请求落在
// `composables/useTopicComputeChoice.ts`（那一份在 `useTopicComputeChoice.spec.ts` 里
// 单独测）。这一份测它这一侧：画出什么、点一下往上发什么，以及父层递下来的
// saving / error / proposal / savedVersion 四种回执各自画成什么样。
import type { ComputeChoice, TopicComputeProfile } from '../types/compute'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { setLocale } from '../i18n'

import TopicComputePicker from './TopicComputePicker.vue'

const cloud: ComputeChoice = {
  name: null,
  profile: 'cloud',
  device_id: null,
}
const lab: ComputeChoice = { ...cloud, name: '实验室工作站', profile: 'device', device_id: 'office' }

const pool = (id: string, label: string, description: string, isDefault: boolean) => ({
  kind: 'compute' as const,
  id,
  label,
  tier: 'byo',
  price: '自备',
  description,
  available: true,
  default: isDefault,
})

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
      pool('device', '自有设备', '在你自己连接的机器上跑。', false),
      pool('cloud', '云端环境', '为房间创建一台云端工作电脑。', true),
    ],
    visibility: {
      options: [],
      effective: null,
      machine_access: false,
    },
    ...overrides,
  }
}

/** 父层递下来的四个回执读口，默认「没在存、没出错、没有提议、没有成功回执」。 */
interface Receipts {
  saving?: boolean
  error?: string
  proposal?: string
  savedVersion?: number
}

function mountPicker(state: TopicComputeProfile, receipts: Receipts = {}) {
  return render(TopicComputePicker, {
    props: { profile: state, saving: false, error: '', proposal: '', savedVersion: 0, ...receipts },
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
    expect(screen.getByRole('button', { name: /^其他配置与设备/ })).toBeTruthy()
  })
  it('sets the machine the whole room runs on', async () => {
    const { emitted } = mountPicker(profile({ project_default: lab }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.getByText(/频道里所有 AI 队友共用这一个环境.*推没推上去都会更换/)).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: /^实验室工作站/ }))
    expect(emitted().select).toHaveLength(1)
    expect(emitted().select[0][0]).toEqual(lab)
    expect(emitted().select[0][1]).toBeUndefined()
  })
  it('stays changeable after the room has started, and moves the room', async () => {
    const working: ComputeChoice = { ...lab, name: '办公室 Mac mini' }
    const { emitted } = mountPicker(
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
    await fireEvent.click(screen.getByRole('button', { name: /^实验室工作站/ }))
    expect(emitted().select).toHaveLength(1)
    expect(emitted().select[0][0]).toEqual(lab)
  })
  it('keeps a named offline default visible without silently substituting cloud', async () => {
    const home = { ...lab, name: '家里那台', device_id: 'home' }
    const { emitted } = mountPicker(profile({ choice: home, project_default: home }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.getByRole('button', { name: /^家里那台.*自有设备.*离线/ })).toBeTruthy()
    expect(emitted().select).toBeUndefined()
  })
  it('is busy while a save is in flight: every row is grey', async () => {
    mountPicker(profile({ project_default: lab }), { saving: true })
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    const rows = screen.getAllByRole('button').filter((b) => /实验室工作站/.test(b.textContent ?? ''))
    expect(rows.length).toBeGreaterThan(0)
    expect(rows.every((r) => (r as HTMLButtonElement).disabled)).toBe(true)
  })
  it('closes the menu when a save succeeds (the receipt comes up)', async () => {
    const state = profile({ project_default: lab })
    const view = mountPicker(state)
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.getByRole('button', { name: /^实验室工作站/ })).toBeTruthy()
    await view.rerender({ profile: state, saving: false, error: '', proposal: '', savedVersion: 1 })
    expect(screen.queryByRole('button', { name: /^实验室工作站/ })).toBeNull()
  })
  it('shows a proposal instead of pretending the room changed', async () => {
    const state = profile({ project_default: lab })
    const { emitted } = mountPicker(state, { proposal: '这一步等 @andyl 点头。' })
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.getByRole('status').textContent).toContain('这一步等 @andyl 点头。')
    // 提议是父层递下来的一个 prop；组件不自己重画、不自己发事件，也不收菜单。
    expect(emitted().select).toBeUndefined()
  })
  it('shows a refused switch and offers no way around it', async () => {
    const { emitted } = mountPicker(profile({ project_default: lab }), { error: '正在准备环境，稍后再换' })
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.getByRole('alert').textContent).toContain('正在准备环境')
    expect(screen.queryByRole('button', { name: /不推送/ })).toBeNull()
    expect(emitted().select).toBeUndefined()
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
    const { emitted } = mountPicker(onLab({ owned: true, sandbox_unavailable: null }, 'isolated'))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.getByText('这个频道在 办公室 Mac mini 上能看到什么')).toBeTruthy()
    await fireEvent.click(screen.getByTestId('room-machine-host'))
    expect(emitted().select).toHaveLength(1)
    expect(emitted().select[0]).toEqual([lab, 'host'])
  })
  it('keeps the whole machine out of reach for anyone but its owner', async () => {
    mountPicker(onLab({ owned: false, sandbox_unavailable: null }, 'isolated'))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    const host = screen.getByTestId('room-machine-host') as HTMLButtonElement
    expect(host.disabled).toBe(true)
    expect(host.textContent).toContain('只有这台电脑的主人能开启')
  })
  it('says a machine has no isolated environment, and where its owner finds why, in the reader language', async () => {
    setLocale('en')
    mountPicker(onLab({ owned: true, sandbox_unavailable: { key: 'sandboxUnavailable' } }, 'isolated'))
    await fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    const isolated = screen.getByTestId('room-machine-isolated') as HTMLButtonElement
    expect(isolated.disabled).toBe(true)
    expect(isolated.textContent).toContain('on their devices page')
    expect((screen.getByTestId('room-machine-host') as HTMLButtonElement).disabled).toBe(false)
  })
  it('names the machine an automatic room is on when access is chosen for it', async () => {
    const automatic: ComputeChoice = { ...lab, name: null, device_id: null }
    const { emitted } = mountPicker({
      ...onLab({ owned: true, sandbox_unavailable: null }, 'host'),
      choice: automatic,
      device_id: 'office',
    })
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    await fireEvent.click(screen.getByTestId('room-machine-isolated'))
    expect(emitted().select).toHaveLength(1)
    expect(emitted().select[0]).toEqual([{ ...automatic, name: '办公室 Mac mini', device_id: 'office' }, 'isolated'])
  })
  it('shows no access choice for a room on cloud', async () => {
    mountPicker(profile())
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    expect(screen.queryByTestId('room-machine-host')).toBeNull()
  })
})

describe('cloud sandbox choice', () => {
  it('asks for no machine size: the cloud is a sandbox, chosen as such', async () => {
    const { emitted } = mountPicker(profile({ choice: lab, project_default: lab }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    await fireEvent.click(screen.getByRole('button', { name: /^其他配置与设备/ }))
    expect(screen.queryByLabelText(/CPU|内存|磁盘/)).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))
    expect(emitted().select).toHaveLength(1)
    expect(emitted().select[0]).toEqual([
      { name: null, profile: 'cloud', device_id: null, whole_machine: false },
      undefined,
    ])
  })
})

describe('whole cloud VM choice', () => {
  async function openForm(state: TopicComputeProfile) {
    const view = mountPicker(state)
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    await fireEvent.click(screen.getByRole('button', { name: /^其他配置与设备/ }))
    await fireEvent.mouseDown(await screen.findByRole('combobox', { name: '环境' }))
    return view
  }

  it('is offered where the deployment has one, and saved as cloud with the whole machine', async () => {
    const { emitted } = await openForm(profile({ choice: lab, project_default: lab, cloud_vm_available: true }))
    await fireEvent.click(await screen.findByRole('option', { name: '整台云虚拟机' }))
    expect(screen.getByText(/每个会话一台独立的云虚拟机，有 sudo，能跑 Docker/)).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))
    expect(emitted().select).toHaveLength(1)
    expect(emitted().select[0]).toEqual([
      { name: null, profile: 'cloud', device_id: null, whole_machine: true },
      undefined,
    ])
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
    const current = screen.getByRole('button', { name: /^整台云虚拟机/ })
    expect(current.textContent).toContain('每个会话一台独立的云虚拟机')
    expect(screen.getByRole('button', { name: /^云端环境/ })).not.toBe(current)
  })
})
