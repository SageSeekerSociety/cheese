import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import Compute from './Compute.vue'

import { listTeamDevices, renameMyDevice, unbindMyDevice } from '@/api'
import i18n, { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

const { confirm } = vi.hoisted(() => ({ confirm: vi.fn() }))
vi.mock('@/api', () => ({ listTeamDevices: vi.fn(), renameMyDevice: vi.fn(), unbindMyDevice: vi.fn() }))
vi.mock('@/lib/desktop', () => ({
  desktopBridge: () => null,
  requestConnecting: vi.fn(),
  deviceFlow: { stage: 'ask' },
}))
vi.mock('@/plugins/dialog', () => ({ useDialog: () => ({ confirm }) }))
const device = { device_id: 'team-computer', name: 'Lab computer', online: true, team_id: 7, screens: [] }
function mount() {
  return render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 7, personal: false }) },
    },
  })
}
beforeEach(() => {
  vi.clearAllMocks()
  setLocale('zh-CN')
  vi.mocked(listTeamDevices).mockResolvedValue({ devices: [device], can_manage: true })
  confirm.mockReturnValue({ wait: async () => true })
})
afterEach(cleanup)

it('lets an admin rename a team device enrolled by any member', async () => {
  const view = mount()
  const input = await view.findByRole('textbox', { name: '设备名称' })
  await fireEvent.update(input, 'Team computer')
  await fireEvent.change(input)
  await waitFor(() => expect(renameMyDevice).toHaveBeenCalledWith('team-computer', 'Team computer'))
})

it('cancelling removal leaves the enrollment working', async () => {
  confirm.mockReturnValue({ wait: async () => false })
  const view = mount()
  await fireEvent.click(await view.findByRole('button', { name: '移除' }))
  expect(confirm).toHaveBeenCalled()
  expect(unbindMyDevice).not.toHaveBeenCalled()
})

it('removes a device only after confirmation', async () => {
  const view = mount()
  await fireEvent.click(await view.findByRole('button', { name: '移除' }))
  await waitFor(() => expect(unbindMyDevice).toHaveBeenCalledWith('team-computer'))
})

it('members can see the team device but cannot change its enrollment', async () => {
  vi.mocked(listTeamDevices).mockResolvedValue({ devices: [device], can_manage: false })
  const view = mount()
  await view.findByText('Lab computer')
  expect(view.queryByRole('textbox')).toBeNull()
  expect(view.queryByRole('button', { name: '移除' })).toBeNull()
  expect(view.queryByRole('button', { name: '添加设备' })).toBeNull()
})
