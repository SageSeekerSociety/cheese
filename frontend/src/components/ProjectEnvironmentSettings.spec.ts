// 「运行环境」那一块：只吃 props、只往上发事件（A 级）。
//
// 取数、保存、状态轮询、「让芝士看看」/重试/采用在
// `composables/useProjectEnvironmentSettings.ts`（那里另有一份行为测试）。编辑草稿
// （那两段脚本与环境变量那几行）留在这里，整份报上去 —— 下面验的就是这个。
import type { EnvironmentDiagnosis, EnvironmentFailure } from '@/types/environment'
import type { EnvironmentStatus, ProjectEnvironmentInfo } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectEnvironmentSettings from './ProjectEnvironmentSettings.vue'

import { setLocale } from '@/i18n'

const UI = { plugins: [createVuetify({ components, directives })] }

const config = {
  setup_script: 'echo setup',
  startup_script: 'echo startup',
  variables: { CUSTOM: 'literal\n$(no expansion)' },
  revision: 'revision-first',
}

const failure: EnvironmentFailure = {
  attempt: 'attempt-1',
  at: new Date().toISOString(),
  stage: 'startup',
  exit_code: 1,
  log: '<script>literal output</script>',
  waiting: 1,
}

function info(overrides: Partial<ProjectEnvironmentInfo> = {}): ProjectEnvironmentInfo {
  return { config, can_edit: true, rooms: [{ id: 'r', title: 'Room', revision: config.revision }], ...overrides }
}

function status(overrides: Partial<EnvironmentStatus> = {}): EnvironmentStatus {
  return {
    state: 'failed',
    stage: 'startup',
    pinned_revision: config.revision,
    log: '<script>literal output</script>',
    exit_code: 1,
    ...overrides,
  }
}

const diagnosis: EnvironmentDiagnosis = {
  reason: '依赖没装全',
  change: '补一步安装',
  setup_script: null,
  startup_script: 'echo new-startup',
  sure: true,
  ran: { setup_script: 'echo setup', startup_script: 'echo startup' },
}

function mount(
  value: ProjectEnvironmentInfo | null,
  props: Partial<{
    status: EnvironmentStatus | null
    selectedRoom: string | null
    error: string
    saving: boolean
    saved: boolean
    saveError: string
    applying: boolean
    diagnosis: EnvironmentDiagnosis | null
    diagnosing: boolean
    agentName: string
  }> = {}
) {
  return render(ProjectEnvironmentSettings, {
    props: {
      info: value,
      status: null,
      selectedRoom: null,
      error: '',
      saving: false,
      saved: false,
      saveError: '',
      applying: false,
      diagnosis: null,
      diagnosing: false,
      agentName: '芝士',
      ...props,
    },
    global: UI,
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  // 下拉要这几样浏览器 API，happy-dom 没有。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ProjectEnvironmentSettings', () => {
  it('reports the whole draft upward without applying a revision', async () => {
    const view = mount(info())
    await fireEvent.click(await view.findByRole('button', { name: '保存配置' }))

    expect(view.emitted('save')).toEqual([
      [
        {
          setup_script: 'echo setup',
          startup_script: 'echo startup',
          variables: [{ key: 'CUSTOM', value: 'literal\n$(no expansion)' }],
        },
      ],
    ])
    expect(view.emitted('apply')).toBeUndefined()
  })

  it('shows the save receipt', async () => {
    const view = mount(info(), { saved: true })
    expect(await view.findByText('已保存。新频道使用这份配置，已有频道保持原配置')).toBeTruthy()
  })

  it('renders logs as text and applies only after the explicit action', async () => {
    const view = mount(info({ rooms: [{ id: 'r', title: 'Room', revision: config.revision }] }), {
      status: status(),
      selectedRoom: 'r',
    })
    expect(await view.findByText('<script>literal output</script>')).toBeTruthy()
    expect(view.container.querySelector('script')).toBeNull()
    await fireEvent.click(await view.findByRole('button', { name: '下次启动时应用' }))
    expect(view.emitted('apply')).toEqual([[true]])
  })

  it('lets a member inspect configuration without edit controls', async () => {
    const view = mount(info({ can_edit: false, rooms: [] }))
    await view.findByDisplayValue('echo setup')
    expect(view.queryByRole('button', { name: '保存配置' })).toBeNull()
    expect(view.queryByRole('button', { name: '添加变量' })).toBeNull()
    expect(view.getByDisplayValue('echo setup').hasAttribute('readonly')).toBe(true)
  })

  it('offers a reload when there was no answer', async () => {
    const view = mount(null, { error: '读不出来了' })
    expect(view.getByText('读不出来了')).toBeTruthy()
    await fireEvent.click(view.getByRole('button', { name: '重新加载' }))
    expect(view.emitted('reload')).toEqual([[]])
  })

  it('reports the picked room and the refresh upward', async () => {
    const view = mount(
      info({
        rooms: [
          { id: 'r', title: 'Room', revision: config.revision },
          { id: 'r2', title: 'Another room', revision: config.revision },
        ],
      }),
      { selectedRoom: 'r' }
    )
    await fireEvent.mouseDown(await view.findByLabelText('查看频道环境'))
    await fireEvent.click(await view.findByRole('option', { name: /Another room/ }))
    expect(view.emitted('select-room')).toEqual([['r2']])

    await fireEvent.click(await view.findByRole('button', { name: '刷新状态' }))
    expect(view.emitted('refresh-status')).toEqual([[]])
  })

  it('hands the failure card up: diagnose and retry', async () => {
    const view = mount(info({ rooms: [{ id: 'r', title: 'Room', revision: config.revision, failure }] }), {
      selectedRoom: 'r',
    })
    await view.findByTestId('environment-failure')
    await fireEvent.click(view.getByTestId('environment-diagnose'))
    expect(view.emitted('diagnose')).toEqual([[]])
    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    expect(view.emitted('retry')).toEqual([[]])
  })

  it('adopts the diagnosis into the scripts and reports the merged draft upward', async () => {
    const view = mount(info({ rooms: [{ id: 'r', title: 'Room', revision: config.revision, failure }] }), {
      selectedRoom: 'r',
      diagnosis,
    })
    await fireEvent.click(await view.findByTestId('environment-adopt'))
    expect(view.emitted('adopt')).toEqual([
      [
        {
          setup_script: 'echo setup',
          startup_script: 'echo new-startup',
          variables: [{ key: 'CUSTOM', value: 'literal\n$(no expansion)' }],
        },
      ],
    ])
  })

  it('asks the failure card to dismiss upward', async () => {
    const view = mount(info({ rooms: [{ id: 'r', title: 'Room', revision: config.revision, failure }] }), {
      selectedRoom: 'r',
      diagnosis,
    })
    await fireEvent.click(await view.findByRole('button', { name: '不用' }))
    expect(view.emitted('dismiss')).toEqual([[]])
  })
})
