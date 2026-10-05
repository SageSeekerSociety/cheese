import type { MyDevice } from '@/cx_types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
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
