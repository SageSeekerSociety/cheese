import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api'

import ProjectSettingsView from './ProjectSettingsView.vue'

vi.mock('../api')
vi.mock('../components/ProjectEnvironmentSettings.vue', () => ({
  default: { template: '<section>运行环境</section>' },
}))
vi.mock('../me', () => ({ myHandle: () => 'alice', myId: () => null }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
}))

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getProject).mockResolvedValue({ name: 'Example' } as Awaited<ReturnType<typeof api.getProject>>)
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
  await vi.waitFor(() => expect(element.textContent).toContain('运行环境'))
  return { element, unmount: () => app.unmount() }
}

describe('project settings', () => {
  it('does not offer project-wide model or role controls', async () => {
    const wrapper = await openSettings()
    expect(wrapper.element.textContent).toContain('运行环境')
    expect(wrapper.element.textContent).not.toContain('项目默认模型')
    expect(wrapper.element.textContent).not.toContain('AI 模型池')
    expect(wrapper.element.textContent).not.toContain('专家角色')
    wrapper.unmount()
  })
})
