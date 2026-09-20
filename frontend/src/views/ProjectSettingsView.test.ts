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
  it.each([true, false])('reflects GitHub protection in the rendered controls (enforced=%s)', async (enforced) => {
    vi.mocked(api.getBranchProtection).mockResolvedValue({
      required_checks: [],
      strict: false,
      dismiss_stale: true,
      auto_merge_allowed: false,
      override_handles: null,
      approvals_required: 1,
      default_reviewer: '',
      merge_method: 'squash',
      github_protection: { enforced, status: enforced ? 'enforced' : 'none' },
    })
    vi.mocked(api.listProjectMembers).mockResolvedValue({ data: [], total: 0 })
    const wrapper = await openSettings()
    try {
      await vi.waitFor(() => expect(wrapper.element.textContent).toContain('暂无必须通过的检查'))
      expect(wrapper.element.textContent?.includes('GitHub 已在执行以下规则')).toBe(enforced)
      const fields = (label: string) => {
        const row = Array.from(wrapper.element.querySelectorAll('.bp-row')).find(
          (element) => element.querySelector('.bp-label')?.textContent === label
        )
        expect(row, label).toBeDefined()
        const inputs = Array.from(row!.querySelectorAll<HTMLInputElement>('input'))
        expect(inputs.length, label).toBeGreaterThan(0)
        return inputs
      }
      for (const label of [
        '合并前必须通过的检查',
        '合并前分支必须跟上 main',
        '新提交作废已有的采纳',
        '人工放行的人',
        '需要几个人批准',
      ]) {
        expect(
          fields(label).every((input) => input.disabled),
          label
        ).toBe(enforced)
      }
      for (const label of ['允许自动合并', '任务默认 reviewer']) {
        expect(
          fields(label).every((input) => !input.disabled),
          label
        ).toBe(true)
      }
      expect(api.setBranchProtection).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('does not offer project-wide model or role controls', async () => {
    const wrapper = await openSettings()
    expect(wrapper.element.textContent).toContain('运行环境')
    expect(wrapper.element.textContent).not.toContain('项目默认模型')
    expect(wrapper.element.textContent).not.toContain('AI 模型池')
    expect(wrapper.element.textContent).not.toContain('专家角色')
    wrapper.unmount()
  })
})
