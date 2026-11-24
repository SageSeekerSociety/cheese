// 项目设置只报告新会话默认环境的选择；取数、保存与广播在 composable 中。
import type { Environment, ProjectComputeConfigs } from '../types/compute'

import { defineComponent, h, markRaw } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { setLocale } from '../i18n'

import ProjectComputeSettings from './ProjectComputeSettings.vue'

const standard: Environment = { kind: 'sandbox', spec: 'standard' }
const vm: Environment = { kind: 'sandbox', spec: 'vm' }

function configs(overrides: Partial<ProjectComputeConfigs> = {}): ProjectComputeConfigs {
  return {
    default: standard,
    max_concurrent_turns: 20,
    max_concurrent_turns_from: 'deployment',
    can_manage: true,
    devices: [{ device_id: 'lab', name: '实验室工作站', online: true, owned: true }],
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

function mount(state: ProjectComputeConfigs | null, props: { error?: string; busy?: boolean; editing?: boolean } = {}) {
  return render(ProjectComputeSettings, {
    props: {
      projectId: 'p1',
      deviceSessionsComponent: markRaw(
        defineComponent({
          props: {
            projectId: { type: String, required: true },
            device: { type: Object, required: true },
            devices: { type: Array, required: true },
            specs: { type: Array, required: true },
          },
          emits: ['changed'],
          setup(switchProps, { emit }) {
            return () =>
              h(
                'button',
                {
                  type: 'button',
                  onClick: () => emit('changed'),
                  'data-switch-props': JSON.stringify(switchProps),
                },
                '查看并更换…'
              )
          },
        })
      ),
      state,
      error: '',
      busy: false,
      editing: false,
      ...props,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

/** How many AI teammates the distribution says run in the environment called `name`. */
function runningIn(name: string): string | null | undefined {
  const row = within(screen.getByTestId('project-distribution'))
    .getAllByRole('listitem')
    .find((item) => within(item).queryByText(name))
  return row && within(row).queryByText(/AI 队友|agents?$/)?.textContent
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('project environment settings', () => {
  it('shows the default for new agents separately from started agents', () => {
    mount(configs())
    expect(screen.getByTestId('project-default').textContent).toContain('标准')
    expect(runningIn('标准')).toBe('3 个 AI 队友')
    expect(runningIn('实验室工作站')).toBe('2 个 AI 队友')
  })

  it('counts sandboxes of each spec apart', () => {
    mount(
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
    expect(runningIn('标准')).toBe('1 个 AI 队友')
    expect(runningIn('虚拟机')).toBe('2 个 AI 队友')
  })

  it('counts one agent as one in English', () => {
    setLocale('en')
    mount(
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
    expect(runningIn('Standard')).toBe('1 agent')
    expect(runningIn('Lab')).toBe('1 agent')
    expect(runningIn('Rig')).toBe('2 agents')
  })

  it('says so when no agent has started', () => {
    mount(configs({ distribution: { sandboxes: [], devices: [] } }))
    expect(screen.getByText('暂无运行中的 AI 队友')).toBeTruthy()
  })

  it('reports the toggle upward when the change button is pressed', async () => {
    const view = mount(configs())
    await fireEvent.click(screen.getByRole('button', { name: '更换' }))
    expect(view.emitted('toggle')).toEqual([[]])
  })

  it('reports the chosen sandbox spec upward', async () => {
    const view = mount(configs(), { editing: true })
    await fireEvent.update(screen.getByRole('radio', { name: '虚拟机' }))
    await fireEvent.click(screen.getByRole('button', { name: '使用这个环境' }))
    expect(view.emitted('save')).toEqual([[vm]])
  })

  it('reports the chosen owned device upward', async () => {
    const view = mount(configs(), { editing: true })
    await fireEvent.update(within(screen.getByTestId('env-self_hosted')).getByRole('radio'))
    await fireEvent.update(within(screen.getByTestId('env-device-lab')).getByRole('radio'))
    await fireEvent.click(screen.getByRole('button', { name: '使用这个环境' }))
    expect(view.emitted('save')).toEqual([[{ kind: 'self_hosted', device_id: 'lab' }]])
  })

  it('cancelling the change saves nothing', async () => {
    const view = mount(configs(), { editing: true })
    await fireEvent.update(screen.getByRole('radio', { name: '虚拟机' }))
    await fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(view.emitted('save')).toBeUndefined()
    expect(view.emitted('toggle')).toEqual([[]])
  })

  it('lets a member who does not manage the project change the default, not move agents off a device', async () => {
    const view = mount(configs({ can_manage: false }))
    await fireEvent.click(screen.getByRole('button', { name: '更换' }))
    expect(view.emitted('toggle')).toEqual([[]])
    expect(screen.queryByRole('button', { name: '查看并更换…' })).toBeNull()
  })

  it('offers device sessions only on device rows and forwards their change', async () => {
    const state = configs()
    const view = mount(state)
    const rows = within(screen.getByTestId('project-distribution')).getAllByRole('listitem')
    expect(within(rows[0]).queryByRole('button', { name: '查看并更换…' })).toBeNull()
    const switchButton = within(rows[1]).getByRole('button', { name: '查看并更换…' })
    expect(JSON.parse(switchButton.getAttribute('data-switch-props')!)).toEqual({
      projectId: 'p1',
      device: { device_id: 'lab', name: '实验室工作站' },
      devices: state.devices,
      specs: state.specs,
    })
    await fireEvent.click(switchButton)
    expect(view.emitted('changed')).toEqual([[]])
  })
})
