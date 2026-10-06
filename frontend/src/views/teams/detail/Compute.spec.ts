import type { MyDevice } from '@/cx_types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'

import Compute from './Compute.vue'

import { listMyDevices, listTeamDevices } from '@/api'
import i18n, { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { handle: 'crew' } }) }))
vi.mock('@/api', () => ({
  listMyDevices: vi.fn(async () => ({ devices: [] })),
  listTeamDevices: vi.fn(async () => ({ devices: [] })),
  registerDeviceForTeam: vi.fn(),
  unregisterDeviceFromTeam: vi.fn(),
}))
vi.mock('@/network/api/teams', () => ({
  TeamsApi: { getComputeProfile: vi.fn(async () => ({ data: { current: 'cloud', profiles: [] } })) },
}))
// 确认框一律说「是」，这样点主操作就直接往下走。
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ confirm: () => ({ wait: async () => true }), custom: vi.fn() }),
}))

beforeAll(() => {
  setLocale('zh-CN')
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})
afterEach(cleanup)

it('shows only self-hosted devices: no cloud machines, no machine quota, no live feed', async () => {
  const opened = vi.fn()
  vi.stubGlobal('WebSocket', opened)
  vi.mocked(listTeamDevices).mockResolvedValueOnce({
    devices: [{ device_id: 'lab', name: 'lab', online: true, project_ids: [], team_ids: [1], screens: [] }],
  })
  const view = render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 1, handle: 'crew', role: 'OWNER' }) },
    },
  })
  expect(await view.findByText('1 台机器 · 1 台在线')).toBeTruthy()
  expect(view.queryByText(/云端|额度|名额/)).toBeNull()
  expect(opened).not.toHaveBeenCalled()
  vi.unstubAllGlobals()
})

it('shows the owner who is on each of their machines, and nobody else', async () => {
  const device = (id: string, inUse: MyDevice['in_use']): MyDevice => ({
    device_id: id,
    name: id,
    online: true,
    project_ids: [],
    team_ids: [1],
    screens: [],
    in_use: inUse,
  })
  vi.mocked(listTeamDevices).mockResolvedValueOnce({
    devices: [
      device('mine', [
        {
          project_id: 'p1',
          project_name: 'Orchard',
          topic_id: 't1',
          topic_title: 'Pricing',
          agent_handle: 'cedar',
          agent_name: 'Cedar',
        },
      ]),
      device('theirs', null),
    ],
  })
  const view = render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 1, handle: 'crew', role: 'MEMBER' }) },
    },
  })

  // 用的那一位是一颗 @chip，点得到它的成员页。
  await vi.waitFor(() => expect(view.container.textContent).toMatch(/正在用：Orchard · Pricing · \s*@Cedar/))
  expect(view.container.querySelector('.device-user .mention')?.textContent).toBe('@Cedar')
  expect(view.getAllByText(/正在用/)).toHaveLength(1)
})

it("lists a device attached only to a project, with the project and its owner's in-use line", async () => {
  vi.mocked(listMyDevices).mockResolvedValueOnce({
    devices: [{ device_id: 'dev-box', name: 'dev-box', online: true, project_ids: ['p1'], team_ids: [], screens: [] }],
  })
  vi.mocked(listTeamDevices).mockResolvedValueOnce({
    devices: [
      {
        device_id: 'dev-box',
        name: 'dev-box',
        online: true,
        project_ids: ['p1'],
        team_ids: [],
        screens: [],
        attached_projects: [
          { id: 'p1', name: 'Orchard' },
          { id: 'p2', name: 'Atlas' },
        ],
        in_use: [
          {
            project_id: 'p1',
            project_name: 'Orchard',
            topic_id: 't1',
            topic_title: 'Pricing',
            agent_handle: 'cedar',
            agent_name: 'Cedar',
          },
        ],
      },
    ],
  })
  const view = render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 1, handle: 'crew', role: 'MEMBER' }) },
    },
  })

  expect(await view.findByText('仅供 Orchard、Atlas 使用')).toBeTruthy()
  expect(view.getByText('1 台机器 · 1 台在线')).toBeTruthy()
  expect(view.container.textContent).toMatch(/正在用：Orchard · Pricing · \s*@Cedar/)
  // It was never added to the team, so there is nothing to take it out of.
  expect(view.queryByRole('button', { name: '移出团队' })).toBeNull()
})

it('读失败时说没读出来，不画「还没有自有设备」；重试能把它读回来', async () => {
  vi.mocked(listTeamDevices).mockRejectedValueOnce({ status: 500, message: '服务端打盹了' })
  const view = render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 1, handle: 'crew', role: 'OWNER' }) },
    },
  })

  // 原来这里是一条可关的红条，关掉之后屏幕上只剩空态 —— 两种都读成「这台小队没有机器」。
  expect(await view.findByText('加载工作电脑失败')).toBeTruthy()
  expect(view.getByText('服务端打盹了')).toBeTruthy()
  expect(view.queryByText('还没有自有设备')).toBeNull()

  vi.mocked(listTeamDevices).mockResolvedValueOnce({ devices: [] })
  await fireEvent.click(view.getByText('重试'))
  expect(await view.findByText('还没有自有设备')).toBeTruthy()
  expect(view.queryByText('加载工作电脑失败')).toBeNull()
})

it('403 说没权限，不摆重试', async () => {
  vi.mocked(listTeamDevices).mockRejectedValueOnce({ code: 403 })
  const view = render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 1, handle: 'crew', role: 'MEMBER' }) },
    },
  })

  expect(await view.findByText('你没有权限查看')).toBeTruthy()
  expect(view.queryByText('重试')).toBeNull()
})

it('names the agent behind each running screen, falling back to its handle', async () => {
  vi.mocked(listTeamDevices).mockResolvedValueOnce({
    devices: [
      {
        device_id: 'lab',
        name: 'lab',
        online: true,
        project_ids: [],
        team_ids: [1],
        screens: [
          {
            sid: 's1',
            agent_handle: 'cheese-kimi',
            agent_user_id: 'u1',
            project_id: 'p1',
            topic_id: 't1',
            agent_name: 'Kimi',
            agent_name_source: 'human',
          },
          {
            sid: 's2',
            agent_handle: 'room-agent-1',
            agent_user_id: 'u2',
            project_id: null,
            topic_id: null,
            agent_name: null,
            agent_name_source: null,
          },
        ],
      },
    ],
  })
  const view = render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 1, handle: 'crew', role: 'OWNER' }) },
    },
  })
  expect(await view.findByText('运行中 · @Kimi')).toBeTruthy()
  expect(view.getByText('运行中 · @room-agent-1')).toBeTruthy()
  expect(view.queryByText(/@cheese-kimi/)).toBeNull()
})
