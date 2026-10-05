import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import { createPinia, getActivePinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api'
import { SudoCancelledError, withSudo } from '../utils/sudo'

import ProjectSettingsView from './ProjectSettingsView.vue'

import { setLocale } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

const me = vi.hoisted(() => ({ id: null as string | null }))
const router = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  currentRoute: { value: { fullPath: '/projects/project/settings', query: {} } },
}))

vi.mock('../api')
vi.mock('../utils/sudo', () => ({
  SudoCancelledError: class SudoCancelledError extends Error {},
  withSudo: vi.fn(),
}))
vi.mock('../components/ProjectEnvironmentSettings.vue', () => ({
  default: { template: '<section>运行环境</section>' },
}))
vi.mock('../me', () => ({ myHandle: () => 'alice', myId: () => me.id }))
vi.mock('vue-router', () => ({
  useRoute: () => router.currentRoute.value,
  useRouter: () => router,
}))

beforeEach(() => {
  vi.resetAllMocks()
  setLocale('zh-CN')
  router.currentRoute.value.query = {}
  setActivePinia(createPinia())
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

// 设置画在盖住整个窗口的一层里（挂在 body 上），一次画一栏：打开哪一栏由 `section` 定。
async function openSettings(section?: string) {
  const host = document.body.appendChild(document.createElement('div'))
  const app = createApp(ProjectSettingsView, { projectId: 'project', section })
  app.use(createVuetify())
  app.use(getActivePinia()!)
  app.mount(host)
  // 设置读完之后才画出这一栏。
  await vi.waitFor(() => expect(document.body.querySelector('.reveal-gate')).not.toBeNull())
  return {
    element: document.body,
    unmount: () => {
      app.unmount()
      host.remove()
    },
  }
}

describe('project settings', () => {
  it.each([
    ['alice', true],
    ['bob', false],
  ])('offers archiving only to the owner (owner=%s)', async (owner, offered) => {
    useWorkspaceStore().projects = [{ id: 'project', name: '毕业设计', created_at: '', owner_handle: owner }]
    const wrapper = await openSettings()
    try {
      expect(wrapper.element.textContent?.includes('归档项目')).toBe(offered)
    } finally {
      wrapper.unmount()
    }
  })

  it('shows the hosted repository without offering a GitHub repository connection', async () => {
    vi.mocked(api.getForgeConnection).mockResolvedValue({
      kind: 'forgejo',
      connected: true,
      repo: 'project/code',
      url: 'https://forge.example/project/code',
    })
    const wrapper = await openSettings('repository')
    try {
      expect(wrapper.element.querySelector('[data-testid="forge-repository"]')?.textContent).toContain('由平台托管')
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
    const wrapper = await openSettings('merge')
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
        '合并前分支须与 main 同步',
        '新提交作废已有的采纳',
        '人工放行的人',
        '需要几个人批准',
      ]) {
        expect(
          fields(label).every((input) => input.disabled),
          label
        ).toBe(enforced)
      }
      for (const label of ['允许自动合并', '默认审阅']) {
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
      const wrapper = await openSettings('repository')
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
      const wrapper = await openSettings('repository')
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
    expect(wrapper.element.textContent).toContain('工作电脑')
    expect(wrapper.element.textContent).not.toContain('项目默认模型')
    expect(wrapper.element.textContent).not.toContain('AI 模型池')
    expect(wrapper.element.textContent).not.toContain('专家角色')
    wrapper.unmount()
  })

  it('lands on the repository section when GitHub sends the user back, and says what happened', async () => {
    router.currentRoute.value.query = { github_install: 'success', repo: 'octo/demo' }
    const wrapper = await openSettings()
    try {
      await vi.waitFor(() => expect(wrapper.element.textContent).toContain('已连接仓库 octo/demo'))
      expect(wrapper.element.querySelector('[aria-current="page"]')?.textContent).toContain('仓库与署名')
    } finally {
      wrapper.unmount()
    }
  })

  // 每一栏只放一类事：人一次只为一件事来（换队友 / 调机器 / 定合并规则 / 接仓库）。
  it('lists the sections in order, with archiving last and only for the owner', async () => {
    useWorkspaceStore().projects = [{ id: 'project', name: '毕业设计', created_at: '', owner_handle: 'alice' }]
    const wrapper = await openSettings()
    try {
      const nav = wrapper.element.querySelector('nav[aria-label="项目设置"]')!
      const entries = Array.from(nav.querySelectorAll('.so__item'), (a) => a.textContent?.trim())
      expect(entries).toEqual([
        'AI 队友',
        '话题命名',
        '工作电脑',
        '运行环境',
        '合并规则',
        '仓库与署名',
        'MCP 服务器',
        '导出项目',
        '归档项目',
      ])
    } finally {
      wrapper.unmount()
    }
  })

  it.each([
    ['agents', ['AI 队友', '默认模型']],
    // 额度跟着工作电脑走：它答的是「还能跑多久」。
    ['computer', ['默认工作电脑', '额度']],
    // 分支保护是合并规则，不是仓库连接。
    ['merge', ['分支保护']],
    ['repository', ['GitHub 仓库地址', '连接 GitHub 仓库', '提交署名', '连接 GitHub 账号']],
    // 导出打包整个项目，不和任何一栏混在一起。
    ['export', ['导出项目']],
  ])('puts the right blocks in the %s section', async (section, blocks) => {
    const wrapper = await openSettings(section)
    try {
      await vi.waitFor(() =>
        expect(
          Array.from(wrapper.element.querySelectorAll('.page-section-title'), (el) => el.textContent?.trim())
        ).toEqual(blocks)
      )
    } finally {
      wrapper.unmount()
    }
  })
})
