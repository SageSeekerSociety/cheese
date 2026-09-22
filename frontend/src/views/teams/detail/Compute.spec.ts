import type { ProjectMachine } from '@/cx_types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'

import Compute from './Compute.vue'

import { changeProjectMachinePower, createProjectMachine, getTeamResourceQuotas, listProjectMachines } from '@/api'
import { teamDataInjectionKey } from '@/keys'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { teamId: '1' } }) }))
vi.mock('@/api', () => ({
  changeProjectMachinePower: vi.fn(),
  createProjectMachine: vi.fn(),
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

it('switching projects cannot bypass a full team quota', async () => {
  const view = render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives })],
      provide: { [teamDataInjectionKey as symbol]: ref({ role: 'OWNER' }) },
    },
  })
  await fireEvent.click(await view.findByRole('button', { name: '开通云算力' }))
  expect(await view.findByText('团队云虚拟机已使用 50 / 50 台')).toBeTruthy()
  expect(view.getByText('剩余 70 额度')).toBeTruthy()
  const confirm = view.getByRole('button', { name: '确认开通' }) as HTMLButtonElement
  expect(confirm.disabled).toBe(true)
  expect(createProjectMachine).not.toHaveBeenCalled()
  await fireEvent.mouseDown(view.getByRole('combobox'))
  await fireEvent.click(await view.findByRole('option', { name: 'Available' }))
  await waitFor(() => expect(view.getByText('本项目占用 1 台；团队内所有项目共享名额')).toBeTruthy())
  expect(confirm.disabled).toBe(true)
  expect(view.getByText('团队云虚拟机已使用 50 / 50 台')).toBeTruthy()
})

it('shows the resulting team usage before spending a free slot', async () => {
  vi.mocked(getTeamResourceQuotas).mockResolvedValueOnce({
    team_id: 1,
    machines: { used: 30, limit: 50 },
    credits: { unlimited: true, credits_total: 0, credits_used: 0, credits_remaining: 0, tokens_per_credit: 10000 },
    projects: [{ id: 'p1', name: 'Full', machines_used: 20, total_tokens: 0, restricted_credits_remaining: 0 }],
  })
  const view = render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives })],
      provide: { [teamDataInjectionKey as symbol]: ref({ role: 'OWNER' }) },
    },
  })
  await fireEvent.click(await view.findByRole('button', { name: '开通云算力' }))
  expect(await view.findByText('本次创建后，团队占用 31 / 50 台')).toBeTruthy()
  expect((view.getByRole('button', { name: '确认开通' }) as HTMLButtonElement).disabled).toBe(false)
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
      plugins: [createVuetify({ components, directives })],
      provide: { [teamDataInjectionKey as symbol]: ref({ role: 'OWNER' }) },
    },
  })
  await fireEvent.click(await view.findByRole('button', { name: '休眠' }))
  expect(changeProjectMachinePower).toHaveBeenCalledWith('p1', 'm1', 'suspend')
  await fireEvent.click(await view.findByRole('button', { name: '恢复' }))
  expect(changeProjectMachinePower).toHaveBeenCalledWith('p1', 'm1', 'resume')
  expect(await view.findByRole('button', { name: '休眠' })).toBeTruthy()
  vi.restoreAllMocks()
})
