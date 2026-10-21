import type { OwnAgentsSettings } from '@/types/ownAgents'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'

import TeamAgentPicker from './TeamAgentPicker.vue'

import { setLocale } from '@/i18n'

const state: OwnAgentsSettings = {
  can_manage: true,
  agents: [],
  my_connections: [
    {
      id: 'subscription',
      kind: 'subscription',
      name: 'Lab Max',
      plan: 'max',
      model: null,
      service_host: null,
      on_account: false,
      machine_name: 'Laptop',
      online: true,
      added: false,
      allowed_models: ['sonnet', 'opus'],
    },
    {
      id: 'service',
      kind: 'model_service',
      name: 'Research API',
      plan: null,
      model: 'research-model',
      service_host: 'example.test',
      on_account: true,
      machine_name: null,
      online: true,
      added: false,
      allowed_models: ['research-model'],
    },
  ],
  devices: [{ device_id: 'laptop', name: 'Laptop', online: true }],
}

beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
})
beforeEach(() => setLocale('en'))
afterEach(cleanup)

function mount(canManage = true) {
  return render(TeamAgentPicker, {
    props: { state: { ...state, can_manage: canManage }, error: '', saving: false },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}
async function choose(index: number, name: string) {
  await fireEvent.mouseDown(
    screen.getByLabelText(['Model connection', 'Model', 'Execution device'][index]!, { exact: true, selector: 'input' })
  )
  await fireEvent.click(await screen.findByRole('option', { name }))
  await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
}

it('creates a teammate only after connection, model and execution device are selected', async () => {
  const view = mount()
  const add = screen.getByRole('button', { name: 'Add AI teammate' }) as HTMLButtonElement
  expect(add.disabled).toBe(true)
  await choose(0, 'Lab Max')
  expect(add.disabled).toBe(true)
  await choose(1, 'opus')
  await choose(2, 'Laptop')
  expect(add.disabled).toBe(false)
  await fireEvent.click(add)
  expect(view.emitted().add).toEqual([['subscription', 'opus', 'laptop']])
})

it('replaces the selected model when switching to a connection that cannot use it', async () => {
  const view = mount()
  await choose(0, 'Lab Max')
  await choose(1, 'opus')
  await choose(2, 'Laptop')
  await choose(0, 'Research API')
  await fireEvent.click(screen.getByRole('button', { name: 'Add AI teammate' }))
  expect(view.emitted().add).toEqual([['service', 'research-model', 'laptop']])
})

it('does not offer creation to a reader without project configuration permission', () => {
  mount(false)
  expect(screen.queryByRole('button', { name: 'Add AI teammate' })).toBeNull()
})
