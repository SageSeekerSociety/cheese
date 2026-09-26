/** 项目设置「话题命名」：两档，选中即保存；不能管理的人只看得见。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getTopicNaming: vi.fn(),
  setTopicNaming: vi.fn(),
}))
vi.mock('../api', () => api)

import { setLocale } from '../i18n'

import ProjectTopicNamingSettings from './ProjectTopicNamingSettings.vue'

beforeEach(() => {
  setLocale('zh-CN')
  vi.resetAllMocks()
  api.getTopicNaming.mockResolvedValue({ mode: 'auto', available: true, can_manage: true })
  api.setTopicNaming.mockImplementation(async (_p: string, mode: string) => ({
    mode,
    available: true,
    can_manage: true,
  }))
})
afterEach(() => cleanup())

function mount() {
  return render(ProjectTopicNamingSettings, {
    props: { projectId: 'p' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

it('defaults to smart naming and saves manual on click', async () => {
  const view = mount()
  const auto = await view.findByRole('radio', { name: /智能命名/ })
  expect(auto.getAttribute('aria-checked')).toBe('true')
  await fireEvent.click(view.getByRole('radio', { name: /手动命名/ }))
  await waitFor(() => expect(api.setTopicNaming).toHaveBeenCalledWith('p', 'manual'))
  await waitFor(() => expect(view.getByRole('radio', { name: /手动命名/ }).getAttribute('aria-checked')).toBe('true'))
})

it('is read-only for someone who cannot manage the project', async () => {
  api.getTopicNaming.mockResolvedValue({ mode: 'auto', available: true, can_manage: false })
  const view = mount()
  const manual = (await view.findByRole('radio', { name: /手动命名/ })) as HTMLButtonElement
  expect(manual.disabled).toBe(true)
  expect(view.getByText('只有项目管理员可以修改。')).toBeTruthy()
})
