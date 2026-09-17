import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import Compute from './Compute.vue'

import { createProjectMachine, getTeamResourceQuotas } from '@/api'
import i18n, { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

const quotas = vi.hoisted(() => ({
  // 默认额度已用满：确认按钮该灰着，切项目也不能绕过去。
  full: {
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
  },
  // 还有名额：用来看「本次创建后会占多少」这一行。
  roomy: {
    team_id: 1,
    machines: { used: 30, limit: 50 },
    credits: { unlimited: true, credits_total: 0, credits_used: 0, credits_remaining: 0, tokens_per_credit: 10000 },
    projects: [{ id: 'p1', name: 'Full', machines_used: 20, total_tokens: 0, restricted_credits_remaining: 0 }],
  },
}))

const teamDevices = vi.hoisted(() => ({
  devices: [
    {
      device_id: 'dev-1',
      name: 'Laptop A',
      online: true,
      team_ids: [1],
      screens: [{ sid: 's1', agent_handle: 'alfred' }],
    },
  ],
}))

const cloudMachines = vi.hoisted(() => ({
  data: [
    {
      id: 'm1',
      project_id: 'p1',
      hostname: 'cloud-1.internal',
      status: 'running',
      ai_status: 'ready',
      cores: 8,
      memory_mb: 16384,
      disk_gb: 128,
      ip: '10.0.0.7',
      device_id: 'cloud-1',
      enroll_error: null,
      enroll_attempts: 1,
      enroll_max_attempts: 3,
    },
  ],
}))

vi.mock('vue-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-router')>()),
  useRoute: () => ({ params: { teamId: '1' } }),
}))
vi.mock('@/api', () => ({
  createProjectMachine: vi.fn(),
  deleteProjectMachine: vi.fn(),
  listMyDevices: vi.fn(async () => teamDevices),
  listTeamDevices: vi.fn(async () => teamDevices),
  listProjects: vi.fn(async () => ({
    data: [
      { id: 'p1', name: 'Full' },
      { id: 'p2', name: 'Available' },
    ],
  })),
  // 每个项目各查一次，只有 p1 有机器——否则两个项目会渲染出两张一模一样的卡片。
  listProjectMachines: vi.fn(async (projectId: string) => ({ data: projectId === 'p1' ? cloudMachines.data : [] })),
  getTeamResourceQuotas: vi.fn(async () => quotas.full),
  registerDeviceForTeam: vi.fn(),
  unregisterDeviceFromTeam: vi.fn(),
}))
vi.mock('@/network/api/teams', () => ({
  TeamsApi: { getComputeProfile: vi.fn(async () => ({ data: { current: 'cloud', profiles: [] } })) },
}))

beforeEach(() => {
  // happy-dom 不给这几个，而 Vuetify 的下拉、对话框和浮层定位会真的去读它们。
  // 必须每条用例都装一次：afterEach 的 unstubAllGlobals 会收走。
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
  setLocale('zh-CN')
  vi.clearAllMocks()
  // 上面那条 clearAllMocks 不动 mockResolvedValueOnce 的队列，遗留的 Once 会被下一个用例吃掉，
  // 所以这里把实现整个重置回默认，用例之间不互相影响。
  vi.mocked(getTeamResourceQuotas).mockReset()
  vi.mocked(getTeamResourceQuotas).mockResolvedValue(quotas.full)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mountPage() {
  return render(Compute, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref({ role: 'OWNER' }) },
    },
  })
}

it('switching projects cannot bypass a full team quota', async () => {
  const view = mountPage()
  await fireEvent.click(await view.findByRole('button', { name: '开通云算力' }))
  expect(await view.findByText('团队云虚拟机已使用 50 / 50 台')).toBeTruthy()
  expect(view.getByText('剩余 70 额度')).toBeTruthy()
  expect(view.getByText('已达到上限，请先释放不再使用的机器')).toBeTruthy()
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
  vi.mocked(getTeamResourceQuotas).mockResolvedValueOnce(quotas.roomy)
  const view = mountPage()
  await fireEvent.click(await view.findByRole('button', { name: '开通云算力' }))
  expect(await view.findByText('本次创建后，团队占用 31 / 50 台')).toBeTruthy()
  expect((view.getByRole('button', { name: '确认开通' }) as HTMLButtonElement).disabled).toBe(false)
})

it('中文下机器卡和设备卡都是中文', async () => {
  const view = mountPage()

  await view.findByText('cloud-1.internal')
  expect(view.getByText('算力')).toBeTruthy()
  expect(view.getByText('运行中')).toBeTruthy()
  expect(view.getByText('8 核')).toBeTruthy()
  expect(view.getByText('16 GB 内存')).toBeTruthy()
  expect(view.getByText('128 GB 磁盘')).toBeTruthy()
  expect(view.getByText('费用归属：Full')).toBeTruthy()
  expect(view.getByText('已接入团队算力池')).toBeTruthy()
  expect(view.getByText('共 1 台 · 1 台在线')).toBeTruthy()
  expect(view.getByText('运行中 · @alfred')).toBeTruthy()
})

it('英文下整页没有汉字', async () => {
  setLocale('en')
  const view = mountPage()

  await view.findByText('cloud-1.internal')
  for (const text of [
    'Compute',
    'Quota and usage',
    'Team cloud machines',
    '50 / 50',
    'Credits left: 70',
    'Used 30 / 100 credits',
    'Cloud compute',
    'Running',
    '8 cores',
    '16 GB memory',
    '128 GB disk',
    'Billed to: Full',
    'Address: 10.0.0.7',
    'In the team pool',
    'Release',
    'Self-hosted devices',
    'Laptop A',
    'Online',
    '1 total · 1 online',
    'Running · @alfred',
    'Remove from team',
  ]) {
    expect(view.getAllByText(text).length).toBeGreaterThan(0)
  }
  expect(CJK.test(document.body.textContent ?? '')).toBe(false)
})

it('英文下开通对话框没有汉字', async () => {
  vi.mocked(getTeamResourceQuotas).mockResolvedValueOnce(quotas.roomy)
  setLocale('en')
  const view = mountPage()

  await fireEvent.click(await view.findByRole('button', { name: 'Provision cloud compute' }))
  expect(await view.findByText('After this, the team uses 31 / 50')).toBeTruthy()
  for (const text of [
    'Team cloud machines used: 30 / 50',
    'This project uses 20 machines; all projects in the team share the quota',
    "Stopping a machine doesn't free its slot — releasing it does",
    'Project billed and audited',
    'CPU cores',
    'Memory MB',
    'Disk GB',
    'Cancel',
    'Provision',
  ]) {
    expect(view.getAllByText(text).length).toBeGreaterThan(0)
  }
  expect(CJK.test(document.body.textContent ?? '')).toBe(false)
})
