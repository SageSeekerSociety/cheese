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
  toast: vi.fn(),
}))
vi.mock('@/api', () => ({ listTeamDevices: mocks.devices }))
vi.mock('@/api/ownAgents', () => ({ checkClaudeCode: mocks.check }))
vi.mock('@/api/modelConnections', () => ({
  listModelConnections: mocks.list,
  startTeamClaudeLogin: mocks.remote,
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
  desktopBridge: () => ({ claudeLogin: mocks.login, enrollmentProfiles: mocks.profiles }),
}))
beforeEach(() => {
  vi.resetAllMocks()
  mocks.list.mockResolvedValue({ connections: [], account_connections: [], platform_models: [], can_manage: true })
  mocks.devices.mockResolvedValue({ devices: [{ device_id: 'enrolled-device', name: 'Laptop', online: true }] })
  mocks.profiles.mockResolvedValue({ 'enrolled-device': 'team-42-3' })
  mocks.remote.mockResolvedValue({ opened: false, command: 'terminal command' })
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
it('logs in with the selected device enrollment profile rather than a guessed team profile', async () => {
  const state = mount()
  await state.login('enrolled-device')
  expect(mocks.login).toHaveBeenCalledWith(false, null, expect.any(Function), 'team-42-3')
  expect(mocks.check).toHaveBeenCalledWith('enrolled-device')
  expect(mocks.remote).not.toHaveBeenCalled()
})
it('refuses local credential changes after team administration permission is removed', async () => {
  mocks.list.mockResolvedValue({ connections: [], account_connections: [], platform_models: [], can_manage: false })
  await mount().login('enrolled-device')
  expect(mocks.login).not.toHaveBeenCalled()
  expect(mocks.remote).not.toHaveBeenCalled()
  expect(mocks.toast).toHaveBeenCalled()
})
it('uses the selected remote device when it has no local enrollment', async () => {
  mocks.profiles.mockResolvedValue({ 'other-device': 'default' })
  const state = mount()
  await state.login('enrolled-device')
  expect(mocks.login).not.toHaveBeenCalled()
  expect(mocks.remote).toHaveBeenCalledWith(42, 'enrolled-device', 'lab')
  expect(state.loginCommand.value).toBe('terminal command')
})
