import type { MyDevice, ProjectMachine } from '@/cx_types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'

import Compute from './Compute.vue'

import { changeProjectMachinePower, listMyDevices, listProjectMachines, listTeamDevices } from '@/api'
import i18n, { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { handle: 'crew' } }) }))
vi.mock('@/api', () => ({
  changeProjectMachinePower: vi.fn(),
  deleteProjectMachine: vi.fn(),
  listMyDevices: vi.fn(async () => ({ devices: [] })),
  listTeamDevices: vi.fn(async () => ({ devices: [] })),
  listProjects: vi.fn(async () => ({
    data: [
      { id: 'p1', name: 'Full' },
      { id: 'p2', name: 'Available' },
    ],
  })),
  listProjectMachines: vi.fn(async () => ({ data: [] })),
  getTeamResourceQuotas: vi.fn(async () => ({
    team_id: 1,
    machines: { used: 50, limit: 50 },
    credits: {
      unlimited: false,
      credits_total: 100,
      credits_used: 30,
      credits_remaining: 70,
      tokens_per_credit: 10000,
    },
    projects: [
      { id: 'p1', name: 'Full', machines_used: 49, total_tokens: 200000, restricted_credits_remaining: 0 },
      { id: 'p2', name: 'Available', machines_used: 1, total_tokens: 100000, restricted_credits_remaining: 0 },
    ],
  })),
  registerDeviceForTeam: vi.fn(),
  unregisterDeviceFromTeam: vi.fn(),
}))
vi.mock('@/network/api/teams', () => ({
  TeamsApi: { getComputeProfile: vi.fn(async () => ({ data: { current: 'cloud', profiles: [] } })) },
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

it('lists the machines projects already have and offers no way to open one', async () => {
  const machine = {
    id: 'm0',
    project_id: 'p2',
    hostname: 'kept-one',
    status: 'running',
    cores: 8,
    memory_mb: 20480,
    disk_gb: 128,
    ai_status: 'ready',
    device_id: 'device-zero',
  } as ProjectMachine
  vi.mocked(listProjectMachines).mockImplementation(
    async (projectId) =>
      ({ data: projectId === 'p2' ? [machine] : [] }) as Awaited<ReturnType<typeof listProjectMachines>>
  )
  const view = render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 1, handle: 'crew', role: 'OWNER' }) },
    },
  })
  expect(await view.findByText('kept-one')).toBeTruthy()
  expect(view.getByText('费用归属：Available')).toBeTruthy()
  expect(view.getByText('8 核')).toBeTruthy()
  expect(view.getByText('团队云端机器')).toBeTruthy()
  expect(view.getByText('50 / 50 台')).toBeTruthy()
  expect(view.queryByRole('button', { name: /开通/ })).toBeNull()
  expect(view.queryByText(/开通云端机器/)).toBeNull()
})

it('shows the owner who is on each of their machines, and nobody else', async () => {
  vi.mocked(listProjectMachines).mockImplementation(
    async () => ({ data: [] as ProjectMachine[] }) as Awaited<ReturnType<typeof listProjectMachines>>
  )
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
  vi.mocked(listProjectMachines).mockImplementation(
    async () => ({ data: [] as ProjectMachine[] }) as Awaited<ReturnType<typeof listProjectMachines>>
  )
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

it('suspends and resumes the same machine through its project', async () => {
  const machine = {
    id: 'm1',
    project_id: 'p1',
    hostname: 'cloud-one',
    status: 'running',
    cores: 2,
    memory_mb: 4096,
    disk_gb: 20,
    ai_status: 'ready',
    device_id: 'device-one',
  } as ProjectMachine
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(listProjectMachines).mockImplementation(
    async (projectId) =>
      ({
        data: projectId === 'p1' ? [machine] : [],
      }) as Awaited<ReturnType<typeof listProjectMachines>>
  )
  vi.mocked(changeProjectMachinePower).mockImplementation(async (_project, _id, operation) => {
    machine.status = operation === 'suspend' ? 'suspended' : 'running'
    return machine
  })
  const view = render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 1, handle: 'crew', role: 'OWNER' }) },
    },
  })
  await fireEvent.click(await view.findByRole('button', { name: '休眠' }))
  expect(changeProjectMachinePower).toHaveBeenCalledWith('p1', 'm1', 'suspend')
  await fireEvent.click(await view.findByRole('button', { name: '恢复' }))
  expect(changeProjectMachinePower).toHaveBeenCalledWith('p1', 'm1', 'resume')
  expect(await view.findByRole('button', { name: '休眠' })).toBeTruthy()
  vi.restoreAllMocks()
})
