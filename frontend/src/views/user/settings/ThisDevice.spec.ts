import { render, screen } from '@testing-library/vue'
import { expect, it, vi } from 'vitest'

import ThisDevice from './ThisDevice.vue'

vi.mock('@/api', () => ({
  listAccessibleDevices: async () => ({
    devices: [
      { device_id: 'a', name: 'Workstation', online: true, team_id: 1 },
      { device_id: 'b', name: 'Workstation', online: true, team_id: 2 },
      { device_id: 'remote', name: 'Other computer', online: true, team_id: 1 },
    ],
  }),
  listMyTeams: async () => [],
  listDeviceRunningSessions: async (id: string) => ({
    sessions: [
      { session_id: id, agent_name: id === 'a' ? 'Ada' : id === 'b' ? 'Grace' : 'Unrelated', roles: ['executor'] },
    ],
  }),
}))
vi.mock('@/lib/desktop', () => ({
  desktopBridge: () => ({
    thisDevices: async () => ['a', 'b', 'hidden'],
    preventSleep: async () => true,
  }),
  deviceFlow: { stage: 'idle', open: false },
}))
vi.mock('./ThisDeviceView.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      props: { sessions: { type: Array, default: () => [] } },
      setup: (props) => () =>
        h(
          'div',
          (props.sessions as { agent_name: string }[]).map((session) => h('span', session.agent_name))
        ),
    }),
  }
})

it('lists work from every visible local enrollment and excludes other computers', async () => {
  const view = render(ThisDevice)
  try {
    expect(await screen.findByText('Ada')).toBeTruthy()
    expect(await screen.findByText('Grace')).toBeTruthy()
    expect(screen.queryByText('Unrelated')).toBeNull()
  } finally {
    view.unmount()
  }
})
