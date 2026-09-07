import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api'

import ProjectSettingsView from './ProjectSettingsView.vue'

vi.mock('../api')
vi.mock('../me', () => ({ myHandle: () => 'alice', myId: () => null }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
}))

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getProject).mockResolvedValue({ name: 'Example' } as Awaited<ReturnType<typeof api.getProject>>)
  vi.mocked(api.getSandboxImage).mockResolvedValue({ current: null, default: 'default', options: [] })
  vi.mocked(api.getUpstream).mockResolvedValue({ url: null })
  vi.mocked(api.listAgentTypes).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(api.listProjectAgents).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(api.getGithubConnection).mockResolvedValue({ connected: false })
})

async function openSettings() {
  const element = document.createElement('div')
  const app = createApp(ProjectSettingsView, { projectId: 'project' })
  app.use(createVuetify())
  app.mount(element)
  await vi.waitFor(() => expect(element.textContent).toContain('项目默认模型'))
  return { element, unmount: () => app.unmount() }
}

describe('project default model', () => {
  it('shows one subscription selector and saves the new default', async () => {
    vi.mocked(api.getModelProfiles).mockResolvedValue({
      supply: 'subscription',
      current: 'sonnet',
      profiles: ['sonnet', 'opus'].map((id) => ({
        id,
        kind: 'model',
        label: id,
        price: '包含',
        tier: 'included',
        description: '',
        available: true,
        default: id === 'sonnet',
      })),
    })
    vi.mocked(api.setModelProfile).mockResolvedValue({ current: 'opus' })
    const wrapper = await openSettings()
    expect(wrapper.element.textContent).toContain('当前使用 Claude 订阅')
    expect(wrapper.element.textContent).not.toContain('AI 模型池')
    expect(api.getExecutionProfiles).not.toHaveBeenCalled()
    const opus = Array.from(wrapper.element.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('opus')
    )!
    opus.click()
    await vi.waitFor(() => expect(opus.textContent).toContain('已设置为默认'))
    expect(api.setModelProfile).toHaveBeenCalledWith('project', 'opus')
    expect(opus.textContent).toContain('已设置为默认')
    wrapper.unmount()
  })

  it('shows platform-managed supply without an inactive Claude choice', async () => {
    vi.mocked(api.getModelProfiles).mockResolvedValue({ supply: 'gateway', current: null, profiles: [] })
    const wrapper = await openSettings()
    expect(wrapper.element.textContent).toContain('当前使用平台模型池，模型由平台统一配置')
    expect(wrapper.element.textContent).not.toContain('Claude 订阅')
    expect(wrapper.element.textContent).not.toContain('已设置为默认')
    expect(api.getExecutionProfiles).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
