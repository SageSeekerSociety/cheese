import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'

import Compute from './Compute.vue'

import { createProjectMachine } from '@/api'
import { teamDataInjectionKey } from '@/keys'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { teamId: '1' } }) }))
vi.mock('@/api', () => ({
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
  listProjectMachines: vi.fn(async (id: string) => ({ data: [], quota: { used: id === 'p1' ? 2 : 1, limit: 2 } })),
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

it('shows quota before allocation and enables allocation only for a project with room', async () => {
  const view = render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives })],
      provide: { [teamDataInjectionKey as symbol]: ref({ role: 'OWNER' }) },
    },
  })
  await fireEvent.click(await view.findByRole('button', { name: '开通云算力' }))
  expect(await view.findByText('该项目云虚拟机已使用 2 / 2 台')).toBeTruthy()
  const confirm = view.getByRole('button', { name: '确认开通' }) as HTMLButtonElement
  expect(confirm.disabled).toBe(true)
  expect(createProjectMachine).not.toHaveBeenCalled()
  await fireEvent.mouseDown(view.getByRole('combobox'))
  await fireEvent.click(await view.findByRole('option', { name: 'Available' }))
  await waitFor(() => expect(confirm.disabled).toBe(false))
  expect(view.getByText('该项目云虚拟机已使用 1 / 2 台')).toBeTruthy()
})
