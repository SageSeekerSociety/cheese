import { defineComponent, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useTeamModels } from './useTeamModels'

import { teamDataInjectionKey } from '@/keys'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  devices: vi.fn(),
  login: vi.fn(),
  profiles: vi.fn(),
  check: vi.fn(),
  remote: vi.fn(),
  personal: vi.fn(),
  native: true,
  toast: vi.fn(),
}))
vi.mock('@/api', () => ({ listTeamDevices: mocks.devices }))
vi.mock('@/api/ownAgents', () => ({ checkClaudeCode: mocks.check }))
vi.mock('@/api/modelConnections', () => ({
  listModelConnections: mocks.list,
  startTeamClaudeLogin: mocks.remote,
  personalConnectionChoices: mocks.personal,
  putPersonalResource: vi.fn(),
  removePersonalResource: vi.fn(),
  addAccountConnection: vi.fn(),
  checkAccountConnection: vi.fn(),
  checkModelConnection: vi.fn(),
  removeAccountConnection: vi.fn(),
  renameAccountConnection: vi.fn(),
  renameModelConnection: vi.fn(),
  revokeModelConnection: vi.fn(),
  setConnectionModels: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: {} }), useRouter: () => ({ replace: vi.fn() }) }))
vi.mock('vuetify-sonner', () => ({ toast: { error: mocks.toast, info: vi.fn() } }))
vi.mock('@/lib/desktop', () => ({
  desktopBridge: () =>
    mocks.native ? { claudeLogin: mocks.login, enrollmentProfiles: mocks.profiles, modelCredentialLogin: true } : null,
}))
beforeEach(() => {
  vi.resetAllMocks()
  mocks.native = true
  mocks.list.mockResolvedValue({ connections: [], account_connections: [], platform_models: [], can_manage: true })
  mocks.devices.mockResolvedValue({ devices: [{ device_id: 'enrolled-device', name: 'Laptop', online: true }] })
  mocks.profiles.mockResolvedValue({ 'enrolled-device': 'team-42-3' })
  mocks.remote.mockResolvedValue({
    ticket: 'authorization',
    server: 'https://example.test/api/connector',
    command: 'terminal command',
  })
  mocks.personal.mockResolvedValue({ connections: [] })
})
afterEach(cleanup)
function mount() {
  let state!: ReturnType<typeof useTeamModels>
  render(
    defineComponent({
      setup() {
        state = useTeamModels()
        return () => null
      },
    }),
    {
      global: { provide: { [teamDataInjectionKey as symbol]: ref({ id: 42, handle: 'lab' }) } },
    }
  )
  return state
}
it('logs in on this computer with its existing profile and the independently authorized team', async () => {
  const state = mount()
  await state.login()
  expect(mocks.login).toHaveBeenCalledWith(
    null,
    expect.any(Function),
    'team-42-3',
    expect.objectContaining({ ticket: 'authorization' })
  )
  expect(mocks.remote).toHaveBeenCalledWith(42)
  expect(mocks.devices).not.toHaveBeenCalled()
})
it('refuses local credential changes after team administration permission is removed', async () => {
  mocks.remote.mockRejectedValue(new Error('Forbidden'))
  await mount().login()
  expect(mocks.login).not.toHaveBeenCalled()
  expect(mocks.toast).toHaveBeenCalled()
})
it('shows the login command on the web without asking for an enrolled device', async () => {
  mocks.native = false
  const state = mount()
  await state.login()
  expect(mocks.login).not.toHaveBeenCalled()
  expect(mocks.remote).toHaveBeenCalledWith(42)
  expect(state.loginCommand.value).toBe('terminal command')
})

it('signs in without enrolling this computer as an execution device', async () => {
  mocks.profiles.mockResolvedValue({})
  await mount().login()
  expect(mocks.login).toHaveBeenCalledWith(
    null,
    expect.any(Function),
    'default',
    expect.objectContaining({ ticket: 'authorization' })
  )
  expect(mocks.devices).not.toHaveBeenCalled()
})
