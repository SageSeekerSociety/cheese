// 项目设置里的工作电脑：只有「新 agent 默认用」和「现在的分布」，没有常用配置。
import type { ComputeChoice, ProjectComputeConfigs } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getProjectComputeConfigs: vi.fn(),
  saveProjectComputeConfigs: vi.fn(),
}))
vi.mock('../api', () => api)

import { setLocale } from '../i18n'

import ProjectComputeSettings from './ProjectComputeSettings.vue'

const cloud: ComputeChoice = {
  name: '云端 · 标准配置',
  profile: 'cloud',
  device_id: null,
  cores: null,
  memory_mb: null,
  disk_gb: null,
}

function configs(overrides: Partial<ProjectComputeConfigs> = {}): ProjectComputeConfigs {
  return {
    default: cloud,
    can_manage: true,
    devices: [{ device_id: 'lab', name: '实验室工作站', online: true }],
    cloud_available: true,
    distribution: {
      cloud: 3,
      devices: [{ device_id: 'lab', name: '实验室工作站', agents: 2, machine_access: true }],
    },
    ...overrides,
  }
}

async function mount() {
  render(ProjectComputeSettings, {
    props: { projectId: 'p1' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return screen.findByTestId('project-default')
}

beforeEach(() => {
  vi.resetAllMocks()
  setLocale('zh-CN')
  // Vuetify 的下拉要这几样浏览器 API，happy-dom 没有。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('matchMedia', () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  }))
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('project work computer settings', () => {
  it('shows only the default for new agents and where started agents are now', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs())
    const row = await mount()

    expect(row.textContent).toContain('新 agent 默认用')
    expect(row.textContent).toContain('云端 · 标准配置')
    expect(screen.getByText('只影响还没开工的 agent；已经开工的 agent 继续用自己那台')).toBeTruthy()
    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText('现在的分布')).toBeTruthy()
    expect(distribution.getByText(/云端 · 3 个 agent/)).toBeTruthy()
    expect(distribution.getByText(/实验室工作站 · 2 个 agent · 能访问整台机器/)).toBeTruthy()
    expect(screen.queryByText(/常用/)).toBeNull()
  })

  it('counts one agent as one in English', async () => {
    setLocale('en')
    api.getProjectComputeConfigs.mockResolvedValue(
      configs({
        distribution: {
          cloud: 1,
          devices: [
            { device_id: 'lab', name: 'Lab', agents: 1, machine_access: false },
            { device_id: 'rig', name: 'Rig', agents: 2, machine_access: false },
          ],
        },
      })
    )
    await mount()

    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText('Cloud · 1 agent')).toBeTruthy()
    expect(distribution.getByText('Lab · 1 agent')).toBeTruthy()
    expect(distribution.getByText('Rig · 2 agents')).toBeTruthy()
  })

  it('says so when no agent has started', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs({ distribution: { cloud: 0, devices: [] } }))
    await mount()

    expect(screen.getByText('暂无开工的 agent')).toBeTruthy()
  })

  it('lets a manager change the default and tells open rooms', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs())
    const lab: ComputeChoice = { ...cloud, name: '实验室工作站', profile: 'device', device_id: 'lab' }
    api.saveProjectComputeConfigs.mockResolvedValue({ default: lab })
    const updated = vi.fn()
    window.addEventListener('project-compute-updated', updated)
    await mount()

    await fireEvent.click(screen.getByRole('button', { name: '更换' }))
    await fireEvent.mouseDown(screen.getByLabelText('工作电脑'))
    await fireEvent.click(await screen.findByRole('option', { name: /实验室工作站/ }))
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))

    await waitFor(() => expect(api.saveProjectComputeConfigs).toHaveBeenCalledWith('p1', { default: lab }))
    expect(api.saveProjectComputeConfigs.mock.calls[0][1]).not.toHaveProperty('favorites')
    await waitFor(() => expect(screen.getByTestId('project-default').textContent).toContain('实验室工作站'))
    expect(updated).toHaveBeenCalledTimes(1)
    window.removeEventListener('project-compute-updated', updated)
  })

  it('does not offer the change to someone who cannot manage the project', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs({ can_manage: false }))
    await mount()

    expect(screen.queryByRole('button', { name: '更换' })).toBeNull()
    expect(screen.getByText('项目负责人可以更换默认')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '查看并更换…' })).toBeNull()
  })

  it('offers a manager to view and switch the agents on each named device', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(
      configs({
        distribution: {
          cloud: 1,
          devices: [
            { device_id: 'lab', name: '实验室工作站', agents: 2, machine_access: true },
            // Picked when those sessions lease; there is no one device to list yet.
            { device_id: null, name: '自有设备 · 自动选择', agents: 1, machine_access: true },
          ],
        },
      })
    )
    await mount()

    const rows = within(screen.getByTestId('project-distribution')).getAllByRole('listitem')
    expect(within(rows[1]).getByRole('button', { name: '查看并更换…' })).toBeTruthy()
    expect(within(rows[0]).queryByRole('button', { name: '查看并更换…' })).toBeNull()
    expect(within(rows[2]).queryByRole('button', { name: '查看并更换…' })).toBeNull()
  })
})
