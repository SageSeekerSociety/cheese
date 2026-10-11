// 项目设置里的环境：只有「新 AI 队友默认使用」和「现在的分布」。沙箱按规格数个数，不说在哪台机器上。
import type { Environment, ProjectComputeConfigs } from '../types/compute'

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

const standard: Environment = { kind: 'sandbox', spec: 'standard' }
const vm: Environment = { kind: 'sandbox', spec: 'vm' }

function configs(overrides: Partial<ProjectComputeConfigs> = {}): ProjectComputeConfigs {
  return {
    default: standard,
    can_manage: true,
    devices: [{ device_id: 'lab', name: '实验室工作站', online: true, owned: false }],
    specs: [
      { spec: 'standard', label: '标准' },
      { spec: 'vm', label: '虚拟机' },
    ],
    distribution: {
      sandboxes: [{ spec: 'standard', label: '标准', agents: 3 }],
      devices: [{ device_id: 'lab', name: '实验室工作站', agents: 2 }],
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

describe('project environment settings', () => {
  it('shows only the default for new agents and where started agents are now', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs())
    const row = await mount()

    expect(row.textContent).toContain('新 AI 队友默认使用')
    expect(row.textContent).toContain('沙箱 · 标准')
    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText(/沙箱 · 标准 · 3 个 AI 队友/)).toBeTruthy()
    expect(distribution.getByText(/实验室工作站 · 2 个 AI 队友/)).toBeTruthy()
  })

  it('counts sandboxes of each spec apart', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(
      configs({
        distribution: {
          sandboxes: [
            { spec: 'standard', label: '标准', agents: 1 },
            { spec: 'vm', label: '虚拟机', agents: 2 },
          ],
          devices: [],
        },
      })
    )
    await mount()

    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText(/沙箱 · 标准 · 1 个 AI 队友/)).toBeTruthy()
    expect(distribution.getByText(/沙箱 · 虚拟机 · 2 个 AI 队友/)).toBeTruthy()
  })

  it('counts one agent as one in English', async () => {
    setLocale('en')
    api.getProjectComputeConfigs.mockResolvedValue(
      configs({
        distribution: {
          sandboxes: [{ spec: 'standard', label: 'Standard', agents: 1 }],
          devices: [
            { device_id: 'lab', name: 'Lab', agents: 1 },
            { device_id: 'rig', name: 'Rig', agents: 2 },
          ],
        },
      })
    )
    await mount()

    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText('Sandbox · Standard · 1 agent')).toBeTruthy()
    expect(distribution.getByText('Lab · 1 agent')).toBeTruthy()
    expect(distribution.getByText('Rig · 2 agents')).toBeTruthy()
  })

  it('says so when no agent has started', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs({ distribution: { sandboxes: [], devices: [] } }))
    await mount()

    expect(screen.getByText('暂无运行中的 AI 队友')).toBeTruthy()
  })

  it('lets a manager change the default and tells open rooms', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs())
    api.saveProjectComputeConfigs.mockResolvedValue({ default: vm })
    const updated = vi.fn()
    window.addEventListener('project-compute-updated', updated)
    await mount()

    await fireEvent.click(screen.getByRole('button', { name: '更换' }))
    await fireEvent.update(document.querySelector('[data-testid="env-spec-vm"] input')!)
    await fireEvent.click(screen.getByRole('button', { name: '使用这个环境' }))

    await waitFor(() => expect(api.saveProjectComputeConfigs).toHaveBeenCalledWith('p1', { default: vm }))
    await waitFor(() => expect(screen.getByTestId('project-default').textContent).toContain('虚拟机'))
    expect(updated).toHaveBeenCalledTimes(1)
    window.removeEventListener('project-compute-updated', updated)
  })

  it('cancelling the change saves nothing', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs())
    await mount()

    await fireEvent.click(screen.getByRole('button', { name: '更换' }))
    await fireEvent.update(document.querySelector('[data-testid="env-spec-vm"] input')!)
    await fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(api.saveProjectComputeConfigs).not.toHaveBeenCalled()
  })

  it('does not offer the change to someone who cannot manage the project', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs({ can_manage: false }))
    await mount()

    expect(screen.queryByRole('button', { name: '更换' })).toBeNull()
    expect(screen.getByText('仅项目负责人可更换默认环境')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '查看并更换…' })).toBeNull()
  })

  it('offers a manager to view and switch the agents on each listed device', async () => {
    api.getProjectComputeConfigs.mockResolvedValue(configs())
    await mount()

    const rows = within(screen.getByTestId('project-distribution')).getAllByRole('listitem')
    // 沙箱不说在哪台机器上，也就没有一台可以打开来换：只有直接跑在某台电脑上的有这颗按钮。
    expect(within(rows[0]).queryByRole('button', { name: '查看并更换…' })).toBeNull()
    expect(within(rows[1]).getByRole('button', { name: '查看并更换…' })).toBeTruthy()
  })
})
