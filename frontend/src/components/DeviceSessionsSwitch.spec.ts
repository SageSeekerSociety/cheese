// 「现在的分布」里一台自有设备上的 agent：选一些，换到另一台工作电脑。换的是它所在
// 的整个房间（一个话题一个容器），走名册那条更换；正在干活的跳过并说明原因，连不上的只由人逐个决定不推送直接更换。
import type { ComputeChoice } from '../types/compute'
import type { DeviceSession } from '../types/deviceSessions'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listDeviceSessions: vi.fn(),
  setTopicComputeChoice: vi.fn(),
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
// 确认框回什么由这一格决定；`confirmAnswer` 是「人点了确定还是取消」。
const dialogMock = vi.hoisted(() => ({ confirm: vi.fn() }))
vi.mock('@/plugins/dialog', async () => ({
  ...(await vi.importActual<typeof import('@/plugins/dialog')>('@/plugins/dialog')),
  useDialog: () => ({ confirm: dialogMock.confirm }),
}))
import { ApiError } from '../api'
import i18n, { setLocale } from '../i18n'

import DeviceSessionsSwitch from './DeviceSessionsSwitch.vue'

const cloud: ComputeChoice = {
  name: null,
  profile: 'cloud',
  device_id: null,
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
    global: { plugins: [createVuetify({ components, directives }), i18n] },
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
  // 默认「点了确定」：取消那一格在下面的用例里单独摆。
  dialogMock.confirm.mockImplementation(() => ({ wait: async () => true }))
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
  // 行里的队友是一颗 @chip，和对话里 @ 到它的那颗一样。
  const metas = Array.from(document.querySelectorAll('.bs-meta')).map((el) =>
    el.textContent?.replace(/\s+/g, ' ').trim()
  )
  expect(metas.some((m) => /^@助手b · 最近活动 .* · 正在运行任务$/.test(m ?? ''))).toBe(true)
  expect(dialog().getByText('另有 2 个 agent 在你打不开的房间里')).toBeTruthy()
  expect(dialog().getByRole('button', { name: '推送并更换' }).hasAttribute('disabled')).toBe(true)

  api.setTopicComputeChoice.mockImplementation(async (topic: string) => {
    if (topic === 'room-b') throw new ApiError(409, '正在运行任务，稍后再换', 'SessionWorking')
    if (topic === 'room-c')
      throw new ApiError(409, '原来那台工作电脑连不上，无法推送改动，没有更换', 'WorkComputerUnreachable')
    return { choice: {}, proposal: null }
  })
  // Vuetify's checkbox reads the input event, as a person's click produces it.
  await fireEvent.input(dialog().getByLabelText('全选'), { target: { checked: true } })
  await fireEvent.click(dialog().getByRole('button', { name: '推送并更换' }))

  await waitFor(() => expect(api.setTopicComputeChoice).toHaveBeenCalledTimes(3))
  expect(api.setTopicComputeChoice.mock.calls.map((call) => [call[0], call[1].profile, call[2]])).toEqual([
    ['room-a', 'cloud', { ifIdle: true, abandonUnpushed: false }],
    ['room-b', 'cloud', { ifIdle: true, abandonUnpushed: false }],
    ['room-c', 'cloud', { ifIdle: true, abandonUnpushed: false }],
  ])
  expect(await dialog().findByText('已更换')).toBeTruthy()
  expect(dialog().getByText('正在运行任务，稍后再换').getAttribute('role')).toBe('alert')
  expect(dialog().getByText('原来那台工作电脑连不上，无法推送改动，没有更换')).toBeTruthy()
  // Only the unreachable one offers the override, and only for itself.
  const [abandon] = dialog().getAllByRole('button', { name: '不推送，直接更换' })
  expect(dialog().getAllByRole('button', { name: '不推送，直接更换' })).toHaveLength(1)

  api.setTopicComputeChoice.mockResolvedValue({ choice: {}, proposal: null })
  await fireEvent.click(abandon)
  // 没推送的改动会留在旧机器上：行里的入口是灰的，这一下确认才是红的。
  expect(dialogMock.confirm).toHaveBeenCalledWith(
    '不推送直接更换的话，原来那台上没推送的改动会留在那台电脑上，不会跟到新电脑。',
    { title: '不推送直接更换？', confirmLabel: '不推送，直接更换', danger: true }
  )
  await waitFor(() =>
    expect(api.setTopicComputeChoice).toHaveBeenLastCalledWith('room-c', expect.anything(), {
      ifIdle: true,
      abandonUnpushed: true,
    })
  )
  await waitFor(() => expect(dialog().getAllByText('已更换')).toHaveLength(2))
  expect(emitted().changed).toBeUndefined()
  await fireEvent.click(dialog().getByRole('button', { name: '取消' }))
  await waitFor(() => expect(emitted().changed).toHaveLength(1))
})

it('asks once more before switching without pushing, and a no changes nothing', async () => {
  api.listDeviceSessions.mockResolvedValue({ sessions: [row('c', '周报')], hidden: 0 })
  api.setTopicComputeChoice.mockRejectedValue(new ApiError(409, '连不上', 'WorkComputerUnreachable'))
  await open()
  await dialog().findByText('周报')

  await fireEvent.input(dialog().getByLabelText('全选'), { target: { checked: true } })
  await fireEvent.click(dialog().getByRole('button', { name: '推送并更换' }))
  await waitFor(() => expect(api.setTopicComputeChoice).toHaveBeenCalledTimes(1))

  dialogMock.confirm.mockImplementation(() => ({ wait: async () => false }))
  await fireEvent.click(dialog().getAllByRole('button', { name: '不推送，直接更换' })[0])
  expect(dialogMock.confirm).toHaveBeenCalledTimes(1)
  // 取消：什么都没换，那条不推送的活儿没有发生。
  expect(api.setTopicComputeChoice).toHaveBeenCalledTimes(1)
  expect(api.setTopicComputeChoice).toHaveBeenCalledWith('room-c', expect.anything(), {
    ifIdle: true,
    abandonUnpushed: false,
  })
})

it('offers every other work computer but the one being left', async () => {
  api.listDeviceSessions.mockResolvedValue({ sessions: [row('a', '定价')], hidden: 0 })
  await open()
  await dialog().findByText('定价')

  await fireEvent.mouseDown(screen.getByLabelText('换到'))

  expect(await screen.findByRole('option', { name: '云端沙箱' })).toBeTruthy()
  expect(screen.getByRole('option', { name: '备用机 · 不可用' })).toBeTruthy()
  expect(screen.queryByRole('option', { name: /旧工作站/ })).toBeNull()
})

it('switches a room once and moves every teammate listed in it', async () => {
  // 一个话题一个容器：同一个房间的两个队友在这台设备上是两行，换的却是同一个房间。
  const second = { ...row('b', '定价'), topic_id: 'room-a' }
  api.listDeviceSessions.mockResolvedValue({ sessions: [row('a', '定价'), second], hidden: 0 })
  api.setTopicComputeChoice.mockResolvedValue({ choice: {}, proposal: null })
  await open()
  await dialog().findAllByText('定价')

  await fireEvent.input(dialog().getByLabelText('全选'), { target: { checked: true } })
  await fireEvent.click(dialog().getByRole('button', { name: '推送并更换' }))

  expect(await dialog().findAllByText('已更换')).toHaveLength(2)
  expect(api.setTopicComputeChoice).toHaveBeenCalledTimes(1)
  expect(api.setTopicComputeChoice.mock.calls[0][0]).toBe('room-a')
})
