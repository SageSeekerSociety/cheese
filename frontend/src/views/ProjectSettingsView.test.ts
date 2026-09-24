import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api'
import { SudoCancelledError, withSudo } from '../utils/sudo'

import ProjectSettingsView from './ProjectSettingsView.vue'

const me = vi.hoisted(() => ({ id: null as string | null }))
const router = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  currentRoute: { value: { fullPath: '/projects/project/settings' } },
}))

vi.mock('../api')
vi.mock('../utils/sudo', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../utils/sudo')>()),
  withSudo: vi.fn(),
}))
vi.mock('../components/ProjectEnvironmentSettings.vue', () => ({
  default: { template: '<section>运行环境</section>' },
}))
vi.mock('../me', () => ({ myHandle: () => 'alice', myId: () => me.id }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => router,
}))

beforeEach(() => {
  vi.resetAllMocks()
  me.id = null
  vi.mocked(api.getUpstream).mockResolvedValue({ url: null })
  vi.mocked(api.listAgentTypes).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(api.listProjectAgents).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(api.getGithubConnection).mockResolvedValue({ connected: false })
  vi.mocked(api.getForgeConnection).mockResolvedValue({ kind: 'github_app', connected: false, repo: null, url: null })
  vi.mocked(api.getForgeAttribution).mockResolvedValue({
    requester_coauthor: null,
    effective: true,
    deployment_default: true,
  })
})

async function openSettings() {
  const element = document.createElement('div')
  const app = createApp(ProjectSettingsView, { projectId: 'project' })
  app.use(createVuetify())
  app.mount(element)
  // 设置读完之后才画出各组；「运行环境」那一组标题出现，就是这一页可以操作了。
  await vi.waitFor(() => expect(element.textContent).toContain('运行环境'))
  return { element, unmount: () => app.unmount() }
}

describe('project settings', () => {
  it('shows the hosted repository without offering a GitHub repository connection', async () => {
    vi.mocked(api.getForgeConnection).mockResolvedValue({
      kind: 'forgejo',
      connected: true,
      repo: 'project/code',
      url: 'https://forge.example/project/code',
    })
    const wrapper = await openSettings()
    try {
      expect(wrapper.element.querySelector('[data-testid="forge-repository"]')?.textContent).toContain('由芝士托管')
      expect(wrapper.element.querySelector('[data-testid="github-repository"]')).toBeNull()
      expect(wrapper.element.querySelector('[data-testid="forge-repository"] a')?.getAttribute('href')).toBe(
        'https://forge.example/project/code'
      )
      expect(wrapper.element.querySelector('[data-testid="forge-attribution"]')?.textContent).toContain('当前已开启')
      expect(api.setForgeAttribution).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

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

  describe('disconnecting the GitHub account', () => {
    const connection = {
      id: 5,
      providerId: 'github_app',
      providerName: 'GitHub',
      providerUserId: '42',
      connectedAt: null,
      login: 'octocat',
      tokenExpires: null,
      hasRefreshToken: false,
    }

    beforeEach(() => {
      me.id = '7'
      vi.mocked(api.listOAuthConnections).mockResolvedValue({ connections: [connection] })
    })

    const disconnect = (element: HTMLElement) =>
      Array.from(element.querySelectorAll('button'))
        .find((b) => b.textContent?.trim() === '断开')!
        .click()

    it('confirms identity for the unlink, then disconnects with the ticket', async () => {
      vi.mocked(withSudo).mockImplementation(async (_purpose, operation) => operation('ticket-1'))
      vi.mocked(api.deleteOAuthConnection).mockResolvedValue()
      const wrapper = await openSettings()
      try {
        await vi.waitFor(() => expect(wrapper.element.textContent).toContain('octocat'))
        disconnect(wrapper.element)

        await vi.waitFor(() => expect(api.deleteOAuthConnection).toHaveBeenCalledWith('7', 5, 'ticket-1'))
        expect(withSudo).toHaveBeenCalledWith('oauth:unbind', expect.any(Function))
        await vi.waitFor(() => expect(wrapper.element.textContent).not.toContain('octocat'))
      } finally {
        wrapper.unmount()
      }
    })

    it('keeps the connection and says nothing when the confirmation is cancelled', async () => {
      vi.mocked(withSudo).mockRejectedValue(new SudoCancelledError())
      const wrapper = await openSettings()
      try {
        await vi.waitFor(() => expect(wrapper.element.textContent).toContain('octocat'))
        const alerts = () => Array.from(wrapper.element.querySelectorAll('[role="alert"]'), (a) => a.textContent)
        const before = alerts()
        disconnect(wrapper.element)

        await vi.waitFor(() => expect(withSudo).toHaveBeenCalled())
        await new Promise((resolve) => setTimeout(resolve))
        expect(api.deleteOAuthConnection).not.toHaveBeenCalled()
        expect(wrapper.element.textContent).toContain('octocat')
        expect(alerts()).toEqual(before)
      } finally {
        wrapper.unmount()
      }
    })
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
