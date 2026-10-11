// 项目设置里的工作电脑：只有「新 agent 默认用」和「现在的分布」，没有常用配置。
//
// 这一件只吃 props、只往上发事件（A 级）：取数与发请求在
// `composables/useProjectComputeSettings.ts`（那里另有一份行为测试），
// 「查看并更换…」那颗按钮由设置页经 `device-sessions` 作用域插槽填进来 —— 这里用一个
// 替身插槽验它出现的位置。
import type { ComputeChoice, ProjectComputeConfigs } from '../types/compute'

import { h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

function mount(state: ProjectComputeConfigs | null, props: { error?: string; busy?: boolean; editing?: boolean } = {}) {
  return render(ProjectComputeSettings, {
    props: { state, error: '', busy: false, editing: false, ...props },
    // 替身插槽：真身 DeviceSessionsSwitch 是另一条线的组件，这里只验插槽出现的位置。
    slots: { 'device-sessions': () => h('button', { type: 'button' }, '查看并更换…') },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
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
    await mount(configs())

    const row = await screen.findByTestId('project-default')
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
    await mount(configs({ distribution: { cloud: 1, cloud_vm: 2, devices: [] } }))

    const distribution = within(await screen.findByTestId('project-distribution'))
    expect(distribution.getByText(/云端环境 · 1 个 AI 队友/)).toBeTruthy()
    expect(distribution.getByText(/整台云虚拟机 · 2 个 AI 队友/)).toBeTruthy()
  })

  it('counts one agent as one in English', async () => {
    setLocale('en')
    await mount(
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

    const distribution = within(await screen.findByTestId('project-distribution'))
    expect(distribution.getByText('Cloud environment · 1 agent')).toBeTruthy()
    expect(distribution.getByText('Lab · 1 agent')).toBeTruthy()
    expect(distribution.getByText('Rig · 2 agents')).toBeTruthy()
  })

  // The platform's choices carry no name: each screen names them in its own
  // language. A device keeps its own name in every language.
  it('names the platform choices in English and keeps a device its own name', async () => {
    setLocale('en')
    await mount(
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

    const row = await screen.findByTestId('project-default')
    expect(row.textContent).toContain('Cloud environment')
    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText('实验室工作站 · 2 agents')).toBeTruthy()
    expect(distribution.getByText('Own device · Picked automatically · 1 agent')).toBeTruthy()
  })

  it('does not show a label an older row stored on a platform choice', async () => {
    setLocale('en')
    await mount(
      configs({
        default: { ...cloud, name: '云端 · 标准配置' },
        distribution: {
          cloud: 0,
          cloud_vm: 0,
          devices: [{ device_id: null, name: '自有设备 · 自动选择', agents: 1, machine_access: false }],
        },
      })
    )

    const row = await screen.findByTestId('project-default')
    expect(row.textContent).toContain('Cloud environment')
    expect(row.textContent).not.toContain('云端')
    const distribution = within(screen.getByTestId('project-distribution'))
    expect(distribution.getByText('Own device · Picked automatically · 1 agent')).toBeTruthy()
  })

  it('says so when no agent has started', async () => {
    await mount(configs({ distribution: { cloud: 0, cloud_vm: 0, devices: [] } }))
    expect(await screen.findByText('暂无运行中的 AI 队友')).toBeTruthy()
  })

  it('reports the toggle upward when the change button is pressed', async () => {
    const view = mount(configs())
    await fireEvent.click(await screen.findByRole('button', { name: '更换' }))
    expect(view.emitted('toggle')).toEqual([[]])
  })

  it('shows 「收起」 while the form is open', async () => {
    await mount(configs(), { editing: true })
    expect(await screen.findByRole('button', { name: '收起' })).toBeTruthy()
  })

  it('reports a chosen default upward, cloud without a name', async () => {
    const view = mount(configs({ default: { ...cloud, name: '实验室工作站', profile: 'device', device_id: 'lab' } }), {
      editing: true,
    })

    await fireEvent.mouseDown(await screen.findByLabelText('环境'))
    await fireEvent.click(await screen.findByRole('option', { name: /^云端/ }))
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))

    expect(view.emitted('save')).toEqual([[{ ...cloud, whole_machine: false }]])
  })

  it('reports a chosen device upward with its own name', async () => {
    const view = mount(configs(), { editing: true })

    await fireEvent.mouseDown(await screen.findByLabelText('环境'))
    await fireEvent.click(await screen.findByRole('option', { name: /实验室工作站/ }))
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))

    expect(view.emitted('save')).toEqual([[{ name: '实验室工作站', profile: 'device', device_id: 'lab' }]])
  })

  it('does not offer the change to someone who cannot manage the project', async () => {
    const view = mount(configs({ can_manage: false }))

    await screen.findByTestId('project-default')
    expect(screen.queryByRole('button', { name: '更换' })).toBeNull()
    expect(screen.getByText('仅项目负责人可更换默认环境')).toBeTruthy()
    expect(view.queryByRole('button', { name: '查看并更换…' })).toBeNull()
  })

  it('fills the device-sessions slot only on a named device, for a manager', async () => {
    await mount(
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

    const rows = await waitFor(() => within(screen.getByTestId('project-distribution')).getAllByRole('listitem'))
    expect(within(rows[1]).getByRole('button', { name: '查看并更换…' })).toBeTruthy()
    expect(within(rows[0]).queryByRole('button', { name: '查看并更换…' })).toBeNull()
    expect(within(rows[2]).queryByRole('button', { name: '查看并更换…' })).toBeNull()
  })
})
