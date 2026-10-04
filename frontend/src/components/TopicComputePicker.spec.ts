// 房间这一项：还没开工的 AI 队友开工时用哪台。开工前后都改得动。
import type { ComputeChoice, TopicComputeProfile } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const setTopicComputeChoice = vi.fn()
const getCloudSupply = vi.fn()
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
  // the cloud-supply read goes through the shared request helper
  request: (...args: unknown[]) => getCloudSupply(...args),
}))

import { setLocale } from '../i18n'

import TopicComputePicker from './TopicComputePicker.vue'

const cloud: ComputeChoice = {
  name: null,
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
    },
    ...overrides,
  }
}

function mountPicker(state: TopicComputeProfile) {
  return render(TopicComputePicker, {
    props: { topicId: 'topic-1', projectId: 'p1', profile: state },
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
  getCloudSupply.mockReset()
})

afterEach(() => cleanup())

describe('room work computer choice', () => {
  it('keeps the default first without listing every team device', async () => {
    mountPicker(profile({ project_default: lab, choice: cloud }))
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    const list = screen.getAllByRole('button').filter((b) => /自有设备|平台标准配置/.test(b.textContent ?? ''))
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

describe('custom cloud spec against the current supply', () => {
  // MicroCloud's offering met with the platform's own limits: memory starts at
  // 512 MB even though the provider would build 128 MB.
  const supply = {
    available: true,
    offering: 'standard-lxc',
    selectable: {
      cores: { min: 1, max: 32 },
      memory_mb: { min: 512, max: 131072 },
      disk_gb: { min: 2, max: 128 },
    },
    provider: {
      cores: { min: 1, max: 32 },
      memory_mb: { min: 128, max: 131072 },
      disk_gb: { min: 2, max: 128 },
    },
    capacity_known: false,
  }

  async function openCustom() {
    mountPicker(profile())
    await fireEvent.click(screen.getByRole('button', { name: '改' }))
    await fireEvent.click(screen.getByRole('button', { name: /其他配置与设备/ }))
    const box = screen.getByLabelText('自定义 CPU、内存和磁盘') as HTMLInputElement
    box.checked = true
    await fireEvent.input(box)
  }
  async function setField(label: string, value: string) {
    await fireEvent.update(screen.getByLabelText(label), value)
  }
  // 菜单收起时这一项还挂着（`v-menu` 用 v-show，不销毁内容），再打开要重新问一次。
  async function reopenMenu() {
    const toggle = screen.getByRole('button', { name: '改' })
    await fireEvent.click(toggle)
    await fireEvent.click(toggle)
  }
  const saveButton = () => screen.getByRole('button', { name: '使用此配置' }) as HTMLButtonElement

  it('shows the range before saving and will not send a spec outside it', async () => {
    getCloudSupply.mockResolvedValue(supply)
    await openCustom()
    expect(getCloudSupply).toHaveBeenCalledWith('/projects/p1/cloud-supply')
    expect((await screen.findByTestId('supply-range')).textContent).toContain('128')
    await setField('磁盘 GB', '256')
    const save = screen.getByRole('button', { name: '使用此配置' })
    await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(true))
    await fireEvent.click(save)
    expect(setTopicComputeChoice).not.toHaveBeenCalled()
  })

  it('offers only what the platform allows, not the provider floor', async () => {
    getCloudSupply.mockResolvedValue(supply)
    await openCustom()
    const range = (await screen.findByTestId('supply-range')).textContent ?? ''
    expect(range).toContain('0.5')
    expect(range).not.toContain('0.125')
  })

  it('sends a spec at the edge of the range unchanged', async () => {
    getCloudSupply.mockResolvedValue(supply)
    setTopicComputeChoice.mockResolvedValue({ choice: cloud, proposal: null })
    await openCustom()
    await screen.findByTestId('supply-range')
    await setField('CPU 核', '32')
    await setField('内存 GB', '128')
    await setField('磁盘 GB', '128')
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))
    await waitFor(() =>
      expect(setTopicComputeChoice).toHaveBeenCalledWith(
        'topic-1',
        expect.objectContaining({ profile: 'cloud', cores: 32, memory_mb: 131072, disk_gb: 128 }),
        {}
      )
    )
  })

  it('says plainly when the range cannot be read, and leaves the check to the cloud', async () => {
    getCloudSupply.mockResolvedValue({ available: false, reason: 'MicroCloud unreachable' })
    setTopicComputeChoice.mockResolvedValue({ choice: cloud, proposal: null })
    await openCustom()
    expect((await screen.findByTestId('supply-unknown')).textContent).toContain('MicroCloud unreachable')
    expect(screen.queryByTestId('supply-range')).toBeNull()
    await setField('磁盘 GB', '256')
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))
    await waitFor(() =>
      expect(setTopicComputeChoice).toHaveBeenCalledWith('topic-1', expect.objectContaining({ disk_gb: 256 }), {})
    )
  })

  // 下面两条是反例：菜单再打开时要重新问一次。把重查去掉，它们就红。
  it('recovers once the cloud answers again instead of staying unreadable for good', async () => {
    getCloudSupply.mockRejectedValueOnce(new Error('MicroCloud unreachable'))
    await openCustom()
    expect((await screen.findByTestId('supply-unknown')).textContent).toContain('MicroCloud unreachable')
    // 查不到的时候可以先保存，256 不该被假范围挡下
    await setField('磁盘 GB', '256')
    await waitFor(() => expect(saveButton().disabled).toBe(false))

    let recovered: (value: unknown) => void = () => {}
    getCloudSupply.mockReturnValueOnce(new Promise((r) => (recovered = r)))
    await reopenMenu()
    expect(getCloudSupply).toHaveBeenCalledTimes(2)
    // 新答案回来之前按钮照旧禁着，不让按旧答案提交
    await waitFor(() => expect(saveButton().disabled).toBe(true))
    recovered(supply)

    // 恢复后的范围顶掉「查不到」；已填的 256 不被清掉，改按新范围判
    expect((await screen.findByTestId('supply-range')).textContent).toContain('128')
    expect(screen.queryByTestId('supply-unknown')).toBeNull()
    expect((screen.getByLabelText('磁盘 GB') as HTMLInputElement).value).toBe('256')
    await waitFor(() => expect(saveButton().disabled).toBe(true))
  })

  it('picks up a widened range instead of blocking a legal spec with the old one', async () => {
    getCloudSupply.mockResolvedValue(supply) // 磁盘上限 128
    await openCustom()
    await screen.findByTestId('supply-range')
    await setField('磁盘 GB', '256')
    await waitFor(() => expect(saveButton().disabled).toBe(true))

    const widened = {
      ...supply,
      selectable: { ...supply.selectable, disk_gb: { min: 2, max: 256 } },
      provider: { ...supply.provider, disk_gb: { min: 2, max: 256 } },
    }
    getCloudSupply.mockResolvedValue(widened)
    await reopenMenu()
    expect(getCloudSupply).toHaveBeenCalledTimes(2)

    // 256 现在合法了：已填值还在，红框消失，按钮放行
    await waitFor(() => expect(saveButton().disabled).toBe(false))
    expect((screen.getByLabelText('磁盘 GB') as HTMLInputElement).value).toBe('256')
    expect(screen.getByTestId('supply-range').textContent ?? '').toContain('256')
  })
})
