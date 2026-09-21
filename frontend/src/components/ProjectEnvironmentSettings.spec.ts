import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getProjectEnvironment: vi.fn(),
  getRoomEnvironment: vi.fn(),
  saveProjectEnvironment: vi.fn(),
  applyRoomEnvironment: vi.fn(),
}))
vi.mock('../api', () => api)

import i18n, { setLocale } from '../i18n'

import ProjectEnvironmentSettings from './ProjectEnvironmentSettings.vue'

const config = {
  setup_script: 'echo setup',
  startup_script: 'echo startup',
  variables: { CUSTOM: 'literal\n$(no expansion)' },
  revision: 'revision-first',
}
beforeEach(() => {
  vi.resetAllMocks()
  // 文案现在从词表来；断言写的是中文，所以语言钉死——不钉就跟着环境走。
  setLocale('zh-CN')
  api.getProjectEnvironment.mockResolvedValue({
    config,
    can_edit: true,
    rooms: [{ id: 'r', title: 'Room', revision: config.revision }],
  })
  api.getRoomEnvironment.mockResolvedValue({
    state: 'failed',
    stage: 'startup',
    pinned_revision: config.revision,
    log: '<script>literal output</script>',
    exit_code: 1,
  })
  api.saveProjectEnvironment.mockResolvedValue({ ...config, revision: 'revision-second' })
  api.applyRoomEnvironment.mockResolvedValue({ state: 'pending' })
})
afterEach(cleanup)

function mount() {
  return render(ProjectEnvironmentSettings, {
    props: { projectId: 'p' },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

it('saves multiline values without applying a revision to an existing room', async () => {
  const view = mount()
  await fireEvent.click(await view.findByRole('button', { name: '保存配置' }))
  await waitFor(() =>
    expect(api.saveProjectEnvironment).toHaveBeenCalledWith('p', {
      setup_script: config.setup_script,
      startup_script: config.startup_script,
      variables: config.variables,
    })
  )
  expect(api.applyRoomEnvironment).not.toHaveBeenCalled()
  expect(await view.findByText('已保存。新房间使用这份配置；已有房间保持当前版本。')).toBeTruthy()
})

it('renders logs as text and applies only after the explicit action', async () => {
  const view = mount()
  expect(await view.findByText('<script>literal output</script>')).toBeTruthy()
  expect(view.container.querySelector('script')).toBeNull()
  await fireEvent.click(await view.findByRole('button', { name: '下次启动时应用' }))
  await waitFor(() => expect(api.applyRoomEnvironment).toHaveBeenCalledWith('p', 'r', true))
})

it('lets a member inspect configuration without edit controls', async () => {
  api.getProjectEnvironment.mockResolvedValue({ config, can_edit: false, rooms: [] })
  const view = mount()
  await view.findByDisplayValue('echo setup')
  expect(view.queryByRole('button', { name: '保存配置' })).toBeNull()
  expect(view.queryByRole('button', { name: '添加变量' })).toBeNull()
  expect(view.getByDisplayValue('echo setup').hasAttribute('readonly')).toBe(true)
})
