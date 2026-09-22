import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getProjectDefaultModel: vi.fn(),
  setProjectDefaultModel: vi.fn(),
}))
vi.mock('../api', () => api)

import { setLocale } from '../i18n'

import ProjectDefaultModelSettings from './ProjectDefaultModelSettings.vue'

const state = {
  model: null as string | null,
  deployment_default: 'sonnet',
  can_manage: true,
  choices: [
    { id: 'sonnet', label: 'Claude Sonnet 5' },
    { id: 'deepseek-flash', label: 'DeepSeek V4.1 Flash' },
  ],
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.resetAllMocks()
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.stubGlobal('devicePixelRatio', 1)
  api.getProjectDefaultModel.mockResolvedValue({ ...state })
  api.setProjectDefaultModel.mockImplementation(async (_project, model) => ({ ...state, model }))
})

it('saves a separate native subagent model without changing the main selection', async () => {
  const view = mount()
  await fireEvent.mouseDown((await view.findAllByRole('combobox'))[1]!)
  await fireEvent.click(await view.findByRole('option', { name: 'DeepSeek V4.1 Flash' }))
  await fireEvent.click(view.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(api.setProjectDefaultModel).toHaveBeenCalledWith('p', null, 'deepseek-flash'))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount() {
  return render(ProjectDefaultModelSettings, {
    props: { projectId: 'p' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

it('saves a selected model and displays it after reloading', async () => {
  const view = mount()
  const save = await view.findByRole('button', { name: '保存' })
  expect(save.hasAttribute('disabled')).toBe(true)
  await fireEvent.mouseDown((await view.findAllByRole('combobox'))[0]!)
  await fireEvent.click(await view.findByRole('option', { name: 'DeepSeek V4.1 Flash' }))
  await waitFor(() => expect(save.hasAttribute('disabled')).toBe(false))
  await fireEvent.click(save)
  await waitFor(() => expect(api.setProjectDefaultModel).toHaveBeenCalledWith('p', 'deepseek-flash', null))
  await waitFor(() => expect(save.hasAttribute('disabled')).toBe(true))
  view.unmount()
  api.getProjectDefaultModel.mockResolvedValue({ ...state, model: 'deepseek-flash' })
  const reloaded = mount()
  expect(await reloaded.findByText('DeepSeek V4.1 Flash')).toBeTruthy()
})

it('shows the deployment default while staging a reset and saves null', async () => {
  api.getProjectDefaultModel.mockResolvedValue({ ...state, model: 'deepseek-flash' })
  const view = mount()
  await fireEvent.click(await view.findByRole('button', { name: '恢复部署默认' }))
  expect(await view.findByText('Claude Sonnet 5')).toBeTruthy()
  expect(api.setProjectDefaultModel).not.toHaveBeenCalled()
  await fireEvent.click(view.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(api.setProjectDefaultModel).toHaveBeenCalledWith('p', null, null))
})
