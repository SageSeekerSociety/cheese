// 「现在的分布」里一台自有设备上的 agent：选一些，换到别的环境。每条会话各换各的，
// 走名册那条更换（先尽力推送一次，推没推上去都换）；正在干活的跳过并说明原因。
import type { DeviceSession } from '../types/deviceSessions'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listDeviceSessions: vi.fn(),
  setSessionEnvironment: vi.fn(),
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
import i18n, { setLocale } from '../i18n'

import DeviceSessionsSwitch from './DeviceSessionsSwitch.vue'

function row(id: string, room: string, working = false): DeviceSession {
  return {
    id,
    topic_id: `room-${id}`,
    topic_title: room,
    agent_handle: `agent-${id}`,
    agent_name: `助手${id}`,
    environment: { kind: 'self_hosted', device_id: 'old' },
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
        { device_id: 'old', name: '旧工作站', online: true, owned: true },
        { device_id: 'spare', name: '备用机', online: false, owned: true },
        { device_id: 'lab', name: '实验室', online: true, owned: false },
      ],
      specs: [
        { spec: 'standard', label: '标准' },
        { spec: 'vm', label: '虚拟机' },
      ],
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await fireEvent.click(screen.getByRole('button', { name: '查看并更换…' }))
  await screen.findByText('「旧工作站」上的 AI 队友')
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
  // 行里的队友只是提到：名字引用，不带 @（@ 只留给真的通知到的那一处）。
  const metas = Array.from(document.querySelectorAll('.bs-meta')).map((el) =>
    el.textContent?.replace(/\s+/g, ' ').trim()
  )
  expect(metas.some((m) => /^助手b · 最近活动 .* · 正在运行任务$/.test(m ?? ''))).toBe(true)
  expect(dialog().getByText('另有 2 个 AI 队友在你打不开的频道里')).toBeTruthy()
  expect(dialog().getByRole('button', { name: '推送并更换' }).hasAttribute('disabled')).toBe(true)

  api.setSessionEnvironment.mockImplementation(async (_topic: string, session: string) => {
    if (session === 'b') throw new ApiError(409, '正在运行任务，稍后再换', 'SessionWorking')
    return {}
  })
  // Vuetify's checkbox reads the input event, as a person's click produces it.
  await fireEvent.input(dialog().getByLabelText('全选'), { target: { checked: true } })
  await fireEvent.click(dialog().getByRole('button', { name: '推送并更换' }))

  await waitFor(() => expect(api.setSessionEnvironment).toHaveBeenCalledTimes(3))
  const auto = { kind: 'sandbox', spec: 'standard' }
  expect(api.setSessionEnvironment.mock.calls).toEqual([
    ['room-a', 'a', auto, { ifIdle: true }],
    ['room-b', 'b', auto, { ifIdle: true }],
    ['room-c', 'c', auto, { ifIdle: true }],
  ])
  expect(await dialog().findAllByText('已更换')).toHaveLength(2)
  expect(dialog().getByText('正在运行任务，稍后再换').getAttribute('role')).toBe('alert')
  // A switch is never refused over unpushed work, so nothing offers to override one.
  expect(dialog().queryByRole('button', { name: /不推送/ })).toBeNull()
  expect(emitted().changed).toBeUndefined()
  await fireEvent.click(dialog().getByRole('button', { name: '取消' }))
  await waitFor(() => expect(emitted().changed).toHaveLength(1))
})

it('offers each sandbox spec and my other computers, not the one being left or anyone else’s', async () => {
  api.listDeviceSessions.mockResolvedValue({ sessions: [row('a', '定价')], hidden: 0 })
  await open()
  await dialog().findByText('定价')

  await fireEvent.mouseDown(screen.getByLabelText('换到'))

  expect(await screen.findByRole('option', { name: '沙箱 · 标准' })).toBeTruthy()
  expect(screen.getByRole('option', { name: '沙箱 · 虚拟机' })).toBeTruthy()
  expect(screen.getByRole('option', { name: '备用机 · 不可用' })).toBeTruthy()
  expect(screen.queryByRole('option', { name: /实验室/ })).toBeNull()
  expect(screen.queryByRole('option', { name: /旧工作站/ })).toBeNull()
})

it('switches only the sessions picked, even when a teammate in the same room is on this machine too', async () => {
  // 每条会话各有各的环境：同一个房间的两个队友在这台设备上是两行，只勾一行就只换那一条。
  const second = { ...row('b', '定价'), topic_id: 'room-a' }
  api.listDeviceSessions.mockResolvedValue({ sessions: [row('a', '定价'), second], hidden: 0 })
  api.setSessionEnvironment.mockResolvedValue({})
  await open()
  await dialog().findAllByText('定价')

  const boxes = dialog().getAllByLabelText('定价')
  await fireEvent.input(boxes[0], { target: { checked: true } })
  await fireEvent.click(dialog().getByRole('button', { name: '推送并更换' }))

  expect(await dialog().findAllByText('已更换')).toHaveLength(1)
  expect(api.setSessionEnvironment).toHaveBeenCalledTimes(1)
  expect(api.setSessionEnvironment.mock.calls[0].slice(0, 2)).toEqual(['room-a', 'a'])
})
