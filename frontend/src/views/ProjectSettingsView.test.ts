import type { Project } from '../cx_types'

import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import { createPinia, getActivePinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api'
import * as ownAgents from '../api/ownAgents'

import ProjectSettingsView from './ProjectSettingsView.vue'

import { setLocale } from '@/i18n'
import { PERMISSIONS } from '@/lib/projectPermissions'
import { seedProjects } from '@/test/seedQueries'

const me = vi.hoisted(() => ({ id: null as string | null }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
const router = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  currentRoute: { value: { fullPath: '/projects/project/settings', query: {} } },
}))

vi.mock('../api')
vi.mock('vuetify-sonner', () => ({ toast }))
vi.mock('../api/ownAgents')
vi.mock('../components/settings/ArchiveProjectSection.vue', () => ({
  // i18n-data: a stand-in that confirms the archive at once
  default: {
    emits: ['archive'],
    template: '<section><button class="archive-confirmed" @click="$emit(\'archive\')">归档</button></section>',
  },
}))
vi.mock('../components/ProjectEnvironmentSettings.vue', () => ({
  // i18n-data: a stand-in for the real block, titled as it is
  default: { template: '<section><span class="page-section-title">脚本与环境变量</span></section>' },
}))
vi.mock('../me', () => ({ myHandle: () => 'alice', myId: () => me.id }))
vi.mock('vue-router', () => ({
  useRoute: () => router.currentRoute.value,
  useRouter: () => router,
}))

/** 我在这个项目里坐哪、能做什么：后端在 `GET /projects/{id}` 上说（#3830）。 */
function asRole(my_role: NonNullable<Project['my_role']>, my_permissions: string[]) {
  vi.mocked(api.getProject).mockResolvedValue({ id: 'project', name: 'P', my_role, my_permissions } as Project)
}
const ALL = PERMISSIONS.map((p) => p.key)

/** 设置目录里的各栏，按显示顺序。项目那一份读回来之前目录还不全，所以先等它读完。 */
async function navEntries(element: HTMLElement): Promise<(string | undefined)[]> {
  await vi.waitFor(() => expect(api.getProject).toHaveBeenCalled())
  await new Promise((resolve) => setTimeout(resolve))
  const nav = element.querySelector('nav[aria-label="项目设置"]')!
  return Array.from(nav.querySelectorAll('.so__item'), (a) => a.textContent?.trim())
}

beforeEach(() => {
  vi.resetAllMocks()
  setLocale('zh-CN')
  router.currentRoute.value.query = {}
  setActivePinia(createPinia())
  me.id = null
  vi.mocked(api.getUpstream).mockResolvedValue({ url: null })
  vi.mocked(ownAgents.getOwnAgents).mockResolvedValue({
    can_manage: false,
    agents: [],
    my_connections: [],
    devices: [],
  })
  asRole('member', [])
  vi.mocked(api.listAgentTypes).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(api.listProjectAgents).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(api.getGithubConnection).mockResolvedValue({ connected: false })
  vi.mocked(api.getForgeConnection).mockResolvedValue({ kind: 'github_app', connected: false, repo: null, url: null })
  vi.mocked(api.getCommitCredit).mockResolvedValue({ mode: 'owner' })
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
    ['owner', true],
    ['admin', false],
    ['member', false],
  ] as const)('offers archiving only to the owner (role=%s)', async (role, offered) => {
    asRole(role, ALL)
    seedProjects([{ id: 'project', name: '毕业设计', created_at: '', owner_handle: 'alice' }])
    const wrapper = await openSettings()
    try {
      expect((await navEntries(wrapper.element)).includes('归档项目')).toBe(offered)
    } finally {
      wrapper.unmount()
    }
  })

  it('shows the hosted repository without offering a GitHub connection or a link to the forge', async () => {
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
      // Nobody has an account on the platform's forge: a link there lands on a sign-in page.
      expect(wrapper.element.querySelector('[data-testid="forge-repository"] a')).toBeNull()
      // Commit credit is the project's own choice, whichever forge holds the code.
      expect(wrapper.element.querySelector('[data-testid="forge-attribution"]')).not.toBeNull()
      expect(api.setCommitCredit).not.toHaveBeenCalled()
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
    vi.mocked(api.listProjectAgents).mockResolvedValue({ data: [], total: 0 })
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

  it('shows the GitHub sign-in account and leaves disconnecting it to account security', async () => {
    me.id = '7'
    vi.mocked(api.listOAuthConnections).mockResolvedValue({
      connections: [
        {
          id: 5,
          providerId: 'github_app',
          providerName: 'GitHub',
          providerUserId: '42',
          connectedAt: null,
          login: 'octocat',
          tokenExpires: null,
          hasRefreshToken: false,
        },
      ],
    })
    const wrapper = await openSettings('repository')
    try {
      await vi.waitFor(() => expect(wrapper.element.textContent).toContain('octocat'))
      const labels = Array.from(wrapper.element.querySelectorAll('button'), (b) => b.textContent?.trim())
      expect(labels).not.toContain('断开')
      expect(labels).not.toContain('解除关联')
    } finally {
      wrapper.unmount()
    }
  })

  it('does not offer project-wide model or role controls', async () => {
    const wrapper = await openSettings()
    expect(wrapper.element.textContent).toContain('环境')
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
  // 「角色和权限」人人看得到，排第一；「频道」「项目地址」各看各的权限；归档只给所有者。
  const REST = ['AI 队友', '任务命名', '环境', '合并规则', '仓库与署名', 'MCP 服务器', '导出项目']
  it.each([
    ['owner', ALL, ['角色和权限', '频道', '项目地址', ...REST, '归档项目']],
    ['admin', ALL, ['角色和权限', '频道', '项目地址', ...REST]],
    ['member', [], ['角色和权限', ...REST]],
    ['member', ['manage_channels'], ['角色和权限', '频道', ...REST]],
    ['member', ['edit_settings'], ['角色和权限', '项目地址', ...REST]],
  ] as const)('lists the sections a %s with %j may use, in order', async (role, permissions, expected) => {
    asRole(role, [...permissions])
    seedProjects([{ id: 'project', name: '毕业设计', created_at: '', owner_handle: 'alice' }])
    const wrapper = await openSettings()
    try {
      expect(await navEntries(wrapper.element)).toEqual(expected)
    } finally {
      wrapper.unmount()
    }
  })

  // 归档完不回首页：首页会把人带进上次待的另一个项目，像是归档没发生、又像跳错了地方。
  it('after archiving, says so and opens the archived projects, where this one now is', async () => {
    asRole('owner', ALL)
    seedProjects([{ id: 'project', name: '毕业设计', created_at: '', owner_handle: 'alice' }])
    vi.mocked(api.archiveProject).mockResolvedValue(undefined as never)
    const wrapper = await openSettings('archive')
    try {
      // 确认框（打项目名）是 ArchiveProjectDialog 自己的事，这里替身直接说「确认归档」。
      const confirm = await vi.waitFor(() => {
        const el = document.body.querySelector<HTMLButtonElement>('.archive-confirmed')
        if (!el) throw new Error('archive section not drawn yet')
        return el
      })
      confirm.click()

      await vi.waitFor(() => expect(router.replace).toHaveBeenCalledWith({ name: 'my-archived-projects' }))
      expect(api.archiveProject).toHaveBeenCalledWith('project')
      expect(toast.success).toHaveBeenCalledWith('已归档「毕业设计」')
    } finally {
      wrapper.unmount()
    }
  })

  it.each([
    // 在哪运行、怎么准备、还能跑多久：环境一页说完。
    ['environment', ['默认环境', '脚本与环境变量', '额度', '项目和成员预算']],
    // 分支保护是合并规则，不是仓库连接。
    ['merge', ['分支保护']],
    ['repository', ['GitHub 仓库地址', '连接 GitHub 仓库', '提交署名']],
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
