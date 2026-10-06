// 项目设置里的工作电脑：只有「新 agent 默认用」和「现在的分布」，没有常用配置。
import type { ComputeChoice, ProjectComputeConfigs } from '../types/compute'

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
  name: null,
  profile: 'cloud',
  device_id: null,
}

function configs(overrides: Partial<ProjectComputeConfigs> = {}): ProjectComputeConfigs {
  return {
    default: cloud,
    can_manage: true,
    devices: [{ device_id: 'lab', name: '实验室工作站', online: true }],
    cloud_available: true,
    cloud_vm_available: false,
    distribution: {
      cloud: 3,
      cloud_vm: 0,
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

    expect(row.textContent).toContain('新 AI 队友默认使用')
    expect(row.textContent).toContain('云端环境')
    expect(screen.getByText('只影响尚未开始运行的 AI 队友，已在运行的继续用原来的环境')).toBeTruthy()
    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText('当前分布')).toBeTruthy()
    expect(distribution.getByText(/云端环境 · 3 个 AI 队友/)).toBeTruthy()
    expect(distribution.getByText(/实验室工作站 · 2 个 AI 队友 · 能访问整台电脑/)).toBeTruthy()
    expect(screen.queryByText(/常用/)).toBeNull()
  })

  it('counts agents on whole cloud VMs apart from those in sandboxes', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs({ distribution: { cloud: 1, cloud_vm: 2, devices: [] } }))
    await mount()

    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText(/云端环境 · 1 个 AI 队友/)).toBeTruthy()
    expect(distribution.getByText(/整台云虚拟机 · 2 个 AI 队友/)).toBeTruthy()
  })

  it('counts one agent as one in English', async () => {
    setLocale('en')
    api.getProjectComputeConfigs.mockResolvedValue(
      configs({
        distribution: {
          cloud: 1,
          cloud_vm: 0,
          devices: [
            { device_id: 'lab', name: 'Lab', agents: 1, machine_access: false },
            { device_id: 'rig', name: 'Rig', agents: 2, machine_access: false },
          ],
        },
      })
    )
    await mount()

    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText('Cloud environment · 1 agent')).toBeTruthy()
    expect(distribution.getByText('Lab · 1 agent')).toBeTruthy()
    expect(distribution.getByText('Rig · 2 agents')).toBeTruthy()
  })

  // The platform's choices carry no name: each screen names them in its own
  // language. A device keeps its own name in every language.
  it('names the platform choices in English and keeps a device its own name', async () => {
    setLocale('en')
    api.getProjectComputeConfigs.mockResolvedValue(
      configs({
        distribution: {
          cloud: 0,
          cloud_vm: 0,
          devices: [
            { device_id: 'lab', name: '实验室工作站', agents: 2, machine_access: false },
            { device_id: null, name: null, agents: 1, machine_access: false },
          ],
        },
      })
    )
    const row = await mount()

    expect(row.textContent).toContain('Cloud environment')
    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText('实验室工作站 · 2 agents')).toBeTruthy()
    expect(distribution.getByText('Own device · Picked automatically · 1 agent')).toBeTruthy()
  })

  it('does not show a label an older row stored on a platform choice', async () => {
    setLocale('en')
    api.getProjectComputeConfigs.mockResolvedValue(
      configs({
        default: { ...cloud, name: '云端 · 标准配置' },
        distribution: {
          cloud: 0,
          cloud_vm: 0,
          devices: [{ device_id: null, name: '自有设备 · 自动选择', agents: 1, machine_access: false }],
        },
      })
    )
    const row = await mount()

    expect(row.textContent).toContain('Cloud environment')
    expect(row.textContent).not.toContain('云端')
    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText('Own device · Picked automatically · 1 agent')).toBeTruthy()
  })

  it('saves the cloud without a name, so no language is stored for everyone', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(
      configs({ default: { ...cloud, name: '实验室工作站', profile: 'device', device_id: 'lab' } })
    )
    api.saveProjectComputeConfigs.mockResolvedValue({ default: cloud })
    await mount()

    await fireEvent.click(screen.getByRole('button', { name: '更换' }))
    await fireEvent.mouseDown(screen.getByLabelText('自有设备'))
    await fireEvent.click(await screen.findByRole('option', { name: /^云端/ }))
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))

    await waitFor(() =>
      expect(api.saveProjectComputeConfigs).toHaveBeenCalledWith('p1', { default: { ...cloud, whole_machine: false } })
    )
  })

  it('says so when no agent has started', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs({ distribution: { cloud: 0, cloud_vm: 0, devices: [] } }))
    await mount()

    expect(screen.getByText('暂无运行中的 AI 队友')).toBeTruthy()
  })

  it('lets a manager change the default and tells open rooms', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs())
    const lab: ComputeChoice = { ...cloud, name: '实验室工作站', profile: 'device', device_id: 'lab' }
    api.saveProjectComputeConfigs.mockResolvedValue({ default: lab })
    const updated = vi.fn()
    window.addEventListener('project-compute-updated', updated)
    await mount()

    await fireEvent.click(screen.getByRole('button', { name: '更换' }))
    await fireEvent.mouseDown(screen.getByLabelText('自有设备'))
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
    expect(screen.getByText('仅项目负责人可更换默认环境')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '查看并更换…' })).toBeNull()
  })

  it('offers a manager to view and switch the agents on each named device', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(
      configs({
        distribution: {
          cloud: 1,
          cloud_vm: 0,
          devices: [
            { device_id: 'lab', name: '实验室工作站', agents: 2, machine_access: true },
            // Picked when those sessions lease; there is no one device to list yet.
            { device_id: null, name: null, agents: 1, machine_access: true },
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
