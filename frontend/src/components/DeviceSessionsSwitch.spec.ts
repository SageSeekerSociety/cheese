// 「现在的分布」里一台自有设备上的 agent：选一些，换到另一台工作电脑。每一个走名册
// 那条更换；正在干活的跳过并说明原因，连不上的只由人逐个决定不推送直接更换。
import type { ComputeChoice, DeviceSession } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listDeviceSessions: vi.fn(),
  setSessionWorkChoice: vi.fn(),
  ApiError: class extends Error {
    constructor(
      readonly status: number,
      message: string,
      readonly code?: string
    ) {
      super(message)
    }
  },
}))
vi.mock('../api', () => api)
import { ApiError } from '../api'
import { setLocale } from '../i18n'

import DeviceSessionsSwitch from './DeviceSessionsSwitch.vue'

const cloud: ComputeChoice = {
  name: '云端 · 标准配置',
  profile: 'cloud',
  device_id: null,
  cores: null,
  memory_mb: null,
  disk_gb: null,
}
const leaving: ComputeChoice = { ...cloud, name: '旧工作站', profile: 'device', device_id: 'old' }
function row(id: string, room: string, working = false): DeviceSession {
  return {
    id,
    topic_id: `room-${id}`,
    topic_title: room,
    agent_handle: `agent-${id}`,
    agent_name: `助手${id}`,
    choice: leaving,
    last_active: new Date().toISOString(),
    working,
  }
}

async function open() {
  const view = render(DeviceSessionsSwitch, {
    props: {
      projectId: 'p1',
      device: { device_id: 'old', name: '旧工作站' },
      devices: [
        { device_id: 'old', name: '旧工作站', online: true },
        { device_id: 'spare', name: '备用机', online: false },
      ],
      cloudAvailable: true,
      projectDefault: cloud,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await fireEvent.click(screen.getByRole('button', { name: '查看并更换…' }))
  await screen.findByText('「旧工作站」上的 agent')
  return view
}
function dialog() {
  return within(screen.getByRole('dialog'))
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('matchMedia', () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  }))
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  setLocale('zh-CN')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('switches the selected agents one by one and says what happened to each', async () => {
  api.listDeviceSessions.mockResolvedValue({
    sessions: [row('a', '定价'), row('b', '排期', true), row('c', '周报')],
    hidden: 2,
  })
  const { emitted } = await open()
  expect(await dialog().findByText('定价')).toBeTruthy()
  expect(dialog().getByText(/助手b · 最近活动 .* · 正在运行任务/)).toBeTruthy()
  expect(dialog().getByText('另有 2 个 agent 在你打不开的房间里')).toBeTruthy()
  expect(dialog().getByRole('button', { name: '推送并更换' }).hasAttribute('disabled')).toBe(true)

  api.setSessionWorkChoice.mockImplementation(async (topic: string) => {
    if (topic === 'room-b') throw new ApiError(409, '正在运行任务，稍后再换', 'SessionWorking')
    if (topic === 'room-c')
      throw new ApiError(409, '原来那台工作电脑连不上，无法推送改动，没有更换', 'WorkComputerUnreachable')
    return { session: {} }
  })
  // Vuetify's checkbox reads the input event, as a person's click produces it.
  await fireEvent.input(dialog().getByLabelText('全选'), { target: { checked: true } })
  await fireEvent.click(dialog().getByRole('button', { name: '推送并更换' }))

  await waitFor(() => expect(api.setSessionWorkChoice).toHaveBeenCalledTimes(3))
  expect(api.setSessionWorkChoice.mock.calls.map((call) => [call[0], call[1], call[2].profile, call[3]])).toEqual([
    ['room-a', 'a', 'cloud', { ifIdle: true, abandonUnpushed: false }],
    ['room-b', 'b', 'cloud', { ifIdle: true, abandonUnpushed: false }],
    ['room-c', 'c', 'cloud', { ifIdle: true, abandonUnpushed: false }],
  ])
  expect(await dialog().findByText('已更换')).toBeTruthy()
  expect(dialog().getByText('正在运行任务，稍后再换').getAttribute('role')).toBe('alert')
  expect(dialog().getByText('原来那台工作电脑连不上，无法推送改动，没有更换')).toBeTruthy()
  // Only the unreachable one offers the override, and only for itself.
  const [abandon] = dialog().getAllByRole('button', { name: '不推送，直接更换' })
  expect(dialog().getAllByRole('button', { name: '不推送，直接更换' })).toHaveLength(1)

  api.setSessionWorkChoice.mockResolvedValue({ session: {} })
  await fireEvent.click(abandon)
  await waitFor(() =>
    expect(api.setSessionWorkChoice).toHaveBeenLastCalledWith('room-c', 'c', expect.anything(), {
      ifIdle: true,
      abandonUnpushed: true,
    })
  )
  expect(await dialog().findAllByText('已更换')).toHaveLength(2)
  expect(emitted().changed).toBeUndefined()
  await fireEvent.click(dialog().getByRole('button', { name: '取消' }))
  await waitFor(() => expect(emitted().changed).toHaveLength(1))
})

it('offers every other work computer but the one being left', async () => {
  api.listDeviceSessions.mockResolvedValue({ sessions: [row('a', '定价')], hidden: 0 })
  await open()
  await dialog().findByText('定价')

  await fireEvent.mouseDown(screen.getByLabelText('换到'))

  expect(await screen.findByRole('option', { name: '云端 · 标准配置' })).toBeTruthy()
  expect(screen.getByRole('option', { name: '备用机 · 不可用' })).toBeTruthy()
  expect(screen.queryByRole('option', { name: /旧工作站/ })).toBeNull()
})
