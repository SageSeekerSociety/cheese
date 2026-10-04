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
  ApiError: class extends Error {
    constructor(
      readonly status: number,
      message: string
    ) {
      super(message)
    }
  },
  authToken: () => 'tok',
  BASE: '/api',
  chatWsUrl: vi.fn(),
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
    projects: [
      { id: 'p1', name: 'Full', machines_used: 49 },
      { id: 'p2', name: 'Available', machines_used: 1 },
    ],
  })),
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

// Stands in for the browser socket: records what the page opened and lets a test
// deliver the frames the team's live feed would send.
class FakeSocket {
  static OPEN = 1
  static opened: FakeSocket[] = []
  readyState = 1
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(readonly url: string) {
    FakeSocket.opened.push(this)
    queueMicrotask(() => this.onopen?.())
  }
  // The server answers every ping; a socket that never did would be replaced.
  send(data: string) {
    if (JSON.parse(data).type === 'ping') queueMicrotask(() => this.deliver({ type: 'pong' }))
  }
  close() {
    this.readyState = 3
  }
  deliver(frame: object) {
    this.onmessage?.({ data: JSON.stringify(frame) })
  }
}
beforeAll(() => vi.stubGlobal('WebSocket', FakeSocket))
afterEach(() => {
  FakeSocket.opened = []
})

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

function mountWith(machines: Record<string, ProjectMachine[]>) {
  vi.mocked(listProjectMachines).mockImplementation(
    async (projectId) => ({ data: machines[projectId] ?? [] }) as Awaited<ReturnType<typeof listProjectMachines>>
  )
  return render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 1, handle: 'crew', role: 'OWNER' }) },
    },
  })
}

const settled = {
  id: 'm-settled',
  project_id: 'p2',
  hostname: 'settled',
  status: 'running',
  ai_status: 'ready',
  device_id: 'device-two',
} as ProjectMachine
const starting = {
  id: 'm-starting',
  project_id: 'p1',
  hostname: 'starting',
  status: 'starting',
  ai_status: 'provisioning',
} as ProjectMachine

function latestSocket(): FakeSocket {
  const socket = FakeSocket.opened.at(-1)
  if (!socket) throw new Error('the page opened no socket')
  return socket
}

it("listens on the team's live feed and asks nothing until it hears of a change", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    const view = mountWith({ p1: [starting], p2: [settled] })
    expect(await view.findByText('starting')).toBeTruthy()
    expect(latestSocket().url).toBe(`ws://${window.location.host}/api/teams/1/live?token=tok`)
    vi.mocked(listProjectMachines).mockClear()

    await vi.advanceTimersByTimeAsync(60_000)
    expect(listProjectMachines).not.toHaveBeenCalled()

    latestSocket().deliver({ type: 'state', resource: 'machines', project_ids: ['p1'] })
    await vi.waitFor(() => expect(listProjectMachines).toHaveBeenCalled())
    expect(vi.mocked(listProjectMachines).mock.calls.map(([id]) => id)).toEqual(['p1'])
    expect(view.getByText('settled')).toBeTruthy()
  } finally {
    vi.useRealTimers()
  }
})

it('reads every project again after the feed reconnects', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    const view = mountWith({ p1: [starting], p2: [settled] })
    expect(await view.findByText('starting')).toBeTruthy()
    vi.mocked(listProjectMachines).mockClear()

    latestSocket().onclose?.()
    await vi.advanceTimersByTimeAsync(2000)

    expect(FakeSocket.opened).toHaveLength(2)
    await vi.waitFor(() =>
      expect(
        vi
          .mocked(listProjectMachines)
          .mock.calls.map(([id]) => id)
          .sort()
      ).toEqual(['p1', 'p2'])
    )
  } finally {
    vi.useRealTimers()
  }
})

it('a refused feed says why and stops asking, instead of retrying into the refusal', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    const view = mountWith({ p1: [starting] })
    expect(await view.findByText('starting')).toBeTruthy()
    vi.mocked(listProjectMachines).mockClear()

    latestSocket().deliver({ type: 'error', code: 'auth_expired', message: '登录状态已失效，请重新登录' })
    latestSocket().onclose?.()
    expect(await view.findByText('登录状态已失效，请重新登录')).toBeTruthy()

    await vi.advanceTimersByTimeAsync(10 * 60_000)
    expect(FakeSocket.opened).toHaveLength(1)
    expect(listProjectMachines).not.toHaveBeenCalled()
  } finally {
    vi.useRealTimers()
  }
})

it('in English a refused feed says why in English', async () => {
  setLocale('en')
  try {
    const view = mountWith({ p1: [starting] })
    expect(await view.findByText('starting')).toBeTruthy()

    latestSocket().deliver({
      type: 'error',
      code: 'auth_expired',
      message: '登录状态已失效，请重新登录',
      i18n: { key: 'signInAgain', params: {} },
    })
    latestSocket().onclose?.()
    expect(await view.findByText('Your sign-in has expired. Sign in again')).toBeTruthy()
  } finally {
    setLocale('zh-CN')
  }
})

it('re-reads every project on the resync while the page is shown', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    const view = mountWith({ p1: [starting], p2: [settled] })
    expect(await view.findByText('starting')).toBeTruthy()
    vi.mocked(listProjectMachines).mockClear()

    await vi.advanceTimersByTimeAsync(5 * 60_000)
    await vi.waitFor(() =>
      expect(
        vi
          .mocked(listProjectMachines)
          .mock.calls.map(([id]) => id)
          .sort()
      ).toEqual(['p1', 'p2'])
    )
  } finally {
    vi.useRealTimers()
  }
})

/** 同一条云端机器，换个身份看：所有者/管理员看得到它的私网地址，普通成员看不到。 */
function cloudMachineWithIp(): ProjectMachine {
  return {
    id: 'm-ip',
    project_id: 'p1',
    hostname: 'with-ip',
    status: 'running',
    cores: 2,
    memory_mb: 4096,
    disk_gb: 20,
    ai_status: 'ready',
    ip: '192.168.31.75',
    device_id: 'device-ip',
  } as ProjectMachine
}

function mountAs(role: string, machine: ProjectMachine) {
  vi.mocked(listProjectMachines).mockImplementation(
    async (projectId) =>
      ({ data: projectId === machine.project_id ? [machine] : [] }) as Awaited<ReturnType<typeof listProjectMachines>>
  )
  return render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 1, handle: 'crew', role }) },
    },
  })
}

it('shows a cloud machine private address to the owner', async () => {
  const view = mountAs('OWNER', cloudMachineWithIp())
  expect(await view.findByText('with-ip')).toBeTruthy()
  expect(view.getByText('地址：192.168.31.75')).toBeTruthy()
})

it('shows a plain member the machine but not its private address', async () => {
  const view = mountAs('MEMBER', cloudMachineWithIp())
  // 卡片照旧：成员看得到机器本身。
  expect(await view.findByText('with-ip')).toBeTruthy()
  // 地址不给看 —— `192.168.x.x` 是机器在网络里的位置，普通成员用不到。
  expect(view.queryByText(/192\.168\.31\.75/)).toBeNull()
  expect(view.queryByText(/地址：/)).toBeNull()
})
