/**
 * 项目设置拆开之前先钉住的那几块。
 *
 * `ProjectSettingsView.vue` 1041 行，要拆进一份 composable 和 `components/settings/`
 * 下面几件。`ProjectSettingsView.test.ts` 已经把整条链子钉住了（归档只给所有者、
 * 托管仓库那一块、GitHub 保护态的灰显、断开账号）；这一份补的是它没盖到、而
 * **这次要挪走**的那几块：
 *
 *   1. 上游仓库地址：保存前去掉首尾空白、清空就等于解绑、失败落在仓库那一块的
 *      提示里而不是把整页顶掉；
 *   2. 分支保护：加一条检查（路径按中英文逗号切分去重）、删一条、批准人数的非法
 *      输入一个请求都不发，和两个开关各自 PUT 自己那一项；
 *   3. 连接仓库的回跳（`?github_install=`）落在仓库那一块的提示里，且查询串读过就
 *      被摘掉；
 *   4. 连接仓库打开安装页，署名入口重新发起 GitHub 登录。
 *
 * 链子和 `ProjectSettingsView.test.ts` 同一条：mock 掉 `../api`，不 mock store。
 * 绿在拆之前的旧文件上；拆完必须原样绿。
 */
import type { BranchProtection } from '../branchProtection'
import type { Project } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api'

import ProjectSettingsView from './ProjectSettingsView.vue'

import { UserAuthApi } from '@/api/userAuth'
import { setLocale } from '@/i18n'

const me = vi.hoisted(() => ({ id: null as string | null }))
const route = vi.hoisted(() => ({ query: {} as Record<string, string | undefined> }))
const router = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  currentRoute: {
    get value() {
      return { fullPath: '/projects/project/settings', path: '/projects/project/settings', query: route.query }
    },
  },
}))
const goAuthorize = vi.hoisted(() => vi.fn(() => true))

vi.mock('../api')
vi.mock('@/api/userAuth', () => ({
  UserAuthApi: {
    getGithubAppIntent: vi.fn(async () => ({ intent: 'ticket-for-alice' })),
    oauthLoginUrl: vi.fn(() => 'https://api.example.test/users/auth/oauth/login/github_app'),
  },
}))
vi.mock('../components/ProjectEnvironmentSettings.vue', () => ({
  default: { template: '<section>运行环境</section>' },
}))
vi.mock('../me', () => ({ myHandle: () => 'alice', myId: () => me.id }))
vi.mock('../lib/desktopApp', () => ({ desktopCan: () => false, goAuthorize, titleBarOverlay: () => false }))
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => router }))

/** 分支保护的 GET 形状：可写的那几项 + 两块只读附注。 */
function rules(overrides: Partial<BranchProtection> = {}): BranchProtection {
  return {
    required_checks: [],
    strict: false,
    dismiss_stale: true,
    auto_merge_allowed: false,
    override_handles: null,
    approvals_required: 1,
    default_reviewer: '',
    merge_method: 'squash',
    github_protection: { enforced: false, status: 'none' },
    ...overrides,
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  setLocale('zh-CN')
  setActivePinia(createPinia())
  me.id = '7'
  route.query = {}
  goAuthorize.mockReturnValue(true)
  vi.mocked(api.getUpstream).mockResolvedValue({ url: null })
  vi.mocked(api.getProject).mockResolvedValue({
    id: 'p1',
    name: 'P',
    created_at: '',
    my_role: 'member',
    my_permissions: [],
  } as Project)
  vi.mocked(api.setUpstream).mockResolvedValue({ url: null })
  vi.mocked(api.getForgeConnection).mockResolvedValue({
    kind: 'github_app',
    connected: false,
    repo: null,
    url: null,
  })
  vi.mocked(api.getCommitCredit).mockResolvedValue({ mode: 'owner' })
  vi.mocked(api.getBranchProtection).mockResolvedValue(rules())
  vi.mocked(api.listProjectMembers).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(api.listOAuthConnections).mockResolvedValue({ connections: [] })
  // PUT 是 partial-update：返回值就是「原来那份叠上改的那几项」。
  vi.mocked(api.setBranchProtection).mockImplementation(async (_id, patch) => ({ ...rules(), ...patch }))
  vi.mocked(api.listAgentTypes).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(api.listProjectAgents).mockResolvedValue({ data: [], total: 0 })
})

// 设置画在盖住整个窗口的一层里（挂在 body 上），一次画一栏：打开哪一栏由 `section` 定，
// 不给就是地址上没写栏的那种打开法。
async function openSettings(section?: string) {
  render(ProjectSettingsView, {
    props: { projectId: 'project', section },
    global: { plugins: [createVuetify({ components, directives }), createPinia()] },
  })
  // 设置读完之后才画出这一栏。
  await waitFor(() => expect(document.body.querySelector('.reveal-gate')).not.toBeNull())
  return { container: document.body }
}

/** 分支保护是各自取数的，等它那一块画出来。 */
const waitForBranchProtection = (container: Element) =>
  waitFor(() => expect(container.textContent).toContain('合并前必须通过的检查'))

/** 按标题找一块设置区：`.page-section` 里那个 `.page-section-title` 是它。 */
function section(container: Element, title: string): HTMLElement {
  const found = Array.from(container.querySelectorAll<HTMLElement>('.page-section')).find(
    (el) => el.querySelector('.page-section-title')?.textContent?.trim() === title
  )
  expect(found, `没有「${title}」那一块`).toBeTruthy()
  return found!
}

function button(container: Element, label: string): HTMLElement {
  const found = Array.from(container.querySelectorAll('button')).find((el) => el.textContent?.trim() === label)
  expect(found, `没有「${label}」这颗按钮`).toBeTruthy()
  return found as HTMLElement
}

/** 分支保护的一行规则：左边 `.bp-label` 是它的名字。 */
function bpRow(container: Element, label: string): HTMLElement {
  const found = Array.from(container.querySelectorAll<HTMLElement>('.bp-row')).find(
    (el) => el.querySelector('.bp-label')?.textContent?.trim() === label
  )
  expect(found, `没有「${label}」那一行`).toBeTruthy()
  return found!
}

function bpInputs(container: Element, label: string): HTMLInputElement[] {
  return Array.from(bpRow(container, label).querySelectorAll<HTMLInputElement>('input'))
}

/** 开关那颗 input：Vuetify 的 checkbox 读的是 input 事件。 */
async function flip(input: HTMLInputElement, on: boolean) {
  input.checked = on
  await fireEvent.input(input)
}

describe('上游仓库地址', () => {
  const upstreamField = (container: Element) =>
    container.querySelector<HTMLInputElement>('input[placeholder^="https://github.com"]')

  it('保存把地址去掉首尾空白之后交给 setUpstream', async () => {
    vi.mocked(api.setUpstream).mockResolvedValue({ url: 'https://github.com/acme/code' })
    const { container } = await openSettings('repository')
    await waitFor(() => expect(upstreamField(container)).toBeTruthy())

    await fireEvent.update(upstreamField(container)!, '  https://github.com/acme/code  ')
    await fireEvent.click(button(container, '保存'))

    await waitFor(() => expect(api.setUpstream).toHaveBeenCalledWith('project', 'https://github.com/acme/code'))
  })

  it('清空再保存走的是解绑：交给后端的是一段空串', async () => {
    vi.mocked(api.getUpstream).mockResolvedValue({ url: 'https://github.com/acme/code' })
    const { container } = await openSettings('repository')
    await waitFor(() => expect(upstreamField(container)?.value).toBe('https://github.com/acme/code'))

    await fireEvent.update(upstreamField(container)!, '   ')
    await fireEvent.click(button(container, '保存'))

    await waitFor(() => expect(api.setUpstream).toHaveBeenCalledWith('project', ''))
  })

  it('保存失败落在仓库那一块的提示里，整页还在', async () => {
    vi.mocked(api.setUpstream).mockRejectedValue(new Error('保存上游仓库失败：网络'))
    const { container } = await openSettings('repository')
    await waitFor(() => expect(upstreamField(container)).toBeTruthy())

    await fireEvent.click(button(container, '保存'))

    // 上游那一块自己没有提示位；它的结果和「连接 GitHub 仓库」共用仓库那一块的提示。
    await waitFor(() => expect(section(container, '连接 GitHub 仓库').textContent).toContain('保存上游仓库失败：网络'))
    // 不是整页的 error 横幅：设置各块照旧在页面上，按钮还能再按一次。
    expect(container.textContent).toContain('合并规则')
    expect(button(container, '保存')).toBeTruthy()
  })
})

describe('分支保护', () => {
  it('加一条必须通过的检查：PUT 带上解析出来的路径，成功后两个输入框清空', async () => {
    const { container } = await openSettings('merge')
    await waitForBranchProtection(container)

    const [name, paths] = bpInputs(container, '合并前必须通过的检查')
    await fireEvent.update(name!, ' pytest ')
    await fireEvent.update(paths!, 'backend/**, tests/，backend/**')
    await fireEvent.click(button(container, '添加'))

    await waitFor(() =>
      expect(api.setBranchProtection).toHaveBeenCalledWith('project', {
        required_checks: [{ name: 'pytest', paths: ['backend/**', 'tests/'] }],
      })
    )
    await waitFor(() => expect(bpInputs(container, '合并前必须通过的检查').map((el) => el.value)).toEqual(['', '']))
    expect(container.querySelector('.bp-check')?.textContent).toContain('pytest')
  })

  it('保存失败时输入框里的字留着，错误挂在规则那一块', async () => {
    vi.mocked(api.setBranchProtection).mockRejectedValueOnce(new Error('保存分支保护规则失败：网络'))
    const { container } = await openSettings('merge')
    await waitForBranchProtection(container)

    await fireEvent.update(bpInputs(container, '合并前必须通过的检查')[0]!, 'pytest')
    await fireEvent.click(button(container, '添加'))

    await waitFor(() => expect(container.textContent).toContain('保存分支保护规则失败：网络'))
    expect(bpInputs(container, '合并前必须通过的检查')[0]!.value).toBe('pytest')
  })

  it('删一条：PUT 里就少掉那一条', async () => {
    vi.mocked(api.getBranchProtection).mockResolvedValue(
      rules({ required_checks: [{ name: 'lint' }, { name: 'test' }] })
    )
    const { container } = await openSettings('merge')
    await waitFor(() => expect(container.querySelectorAll('.bp-check')).toHaveLength(2))

    await fireEvent.click(container.querySelectorAll<HTMLElement>('.bp-check')[0]!.querySelector('button')!)

    await waitFor(() =>
      expect(api.setBranchProtection).toHaveBeenCalledWith('project', { required_checks: [{ name: 'test' }] })
    )
  })

  it('批准人数填了非法值：一个请求都不发，报一句，草稿弹回原值', async () => {
    vi.mocked(api.getBranchProtection).mockResolvedValue(rules({ approvals_required: 2 }))
    const { container } = await openSettings('merge')
    await waitForBranchProtection(container)

    const input = bpInputs(container, '需要几个人批准')[0]!
    await fireEvent.update(input, '0')
    // 这一格是 @change 才保存的（收起输入框再校验，不打字打一半就发请求）。
    await fireEvent.change(input)

    await waitFor(() => expect(container.textContent).toContain('批准人数须为不小于 1 的整数'))
    expect(api.setBranchProtection).not.toHaveBeenCalled()
    await waitFor(() => expect(input.value).toBe('2'))
  })

  it('批准人数填了合法值：PUT 的就是那一项', async () => {
    const { container } = await openSettings('merge')
    await waitForBranchProtection(container)

    const input = bpInputs(container, '需要几个人批准')[0]!
    await fireEvent.update(input, '3')
    await fireEvent.change(input)

    await waitFor(() => expect(api.setBranchProtection).toHaveBeenCalledWith('project', { approvals_required: 3 }))
  })

  it('两个开关各自 PUT 自己那一项', async () => {
    const { container } = await openSettings('merge')
    await waitForBranchProtection(container)

    await flip(bpInputs(container, '合并前分支须与 main 同步')[0]!, true)
    await waitFor(() => expect(api.setBranchProtection).toHaveBeenCalledWith('project', { strict: true }))

    await flip(bpInputs(container, '新提交作废已有的采纳')[0]!, false)
    await waitFor(() => expect(api.setBranchProtection).toHaveBeenCalledWith('project', { dismiss_stale: false }))
  })
})

describe('连接仓库的回跳', () => {
  it('结果画在「连接 GitHub 仓库」那一块，读过就摘掉', async () => {
    route.query = { github_install: 'success', repo: 'acme/code' }
    const { container } = await openSettings()

    await waitFor(() => expect(section(container, '连接 GitHub 仓库').textContent).toContain('已连接仓库 acme/code'))
    // 读过就把查询串摘掉，刷新不会重播。
    expect(router.replace).toHaveBeenCalledWith({ query: {} })
  })
})

describe('两个「连接」入口', () => {
  it('连接仓库：后端说已连接就画出来，并重新读一遍仓库状态', async () => {
    vi.mocked(api.connectGithubRepo).mockResolvedValue({ connected: true, repo: 'acme/code' })
    vi.mocked(api.getForgeConnection)
      .mockResolvedValueOnce({ kind: 'github_app', connected: false, repo: null, url: null })
      .mockResolvedValue({ kind: 'github_app', connected: true, repo: 'acme/code', url: null })
    const { container } = await openSettings('repository')
    await waitFor(() => expect(button(container, '连接 GitHub 仓库')).toBeTruthy())

    await fireEvent.click(button(container, '连接 GitHub 仓库'))

    await waitFor(() => expect(section(container, '连接 GitHub 仓库').textContent).toContain('已连接'))
    expect(api.connectGithubRepo).toHaveBeenCalledWith('project')
    expect(section(container, '连接 GitHub 仓库').textContent).toContain('acme/code')
  })

  it('没有连接 GitHub 时，从署名设置为当前账号发起 GitHub 授权', async () => {
    const { container } = await openSettings('repository')
    await waitFor(() => expect(button(container, '连接 GitHub')).toBeTruthy())

    await fireEvent.click(button(container, '连接 GitHub'))

    await waitFor(() =>
      expect(goAuthorize).toHaveBeenCalledWith(
        'https://api.example.test/users/auth/oauth/login/github_app?intent=ticket-for-alice'
      )
    )
    expect(UserAuthApi.getGithubAppIntent).toHaveBeenCalled()
  })

  it('署名给 AI 队友时，用不到看的人自己的 GitHub 账号', async () => {
    vi.mocked(api.getCommitCredit).mockResolvedValue({ mode: 'agent' })
    const { container } = await openSettings('repository')
    await waitFor(() => expect(section(container, '提交署名')).toBeTruthy())

    expect(container.querySelector('[data-testid="own-github"]')).toBeNull()
  })

  it('打开 AI 队友分栏时不请求未打开的仓库与分支保护接口，且默认模型只读一次', async () => {
    vi.mocked(api.getProjectDefaultModel).mockResolvedValue({
      model: null,
      subagent_model: null,
      deployment_default: 'sonnet',
      choices: [],
    })
    await openSettings('agents')
    await waitFor(() => expect(api.listProjectAgents).toHaveBeenCalledWith('project'))

    expect(api.getUpstream).not.toHaveBeenCalled()
    expect(api.getForgeConnection).not.toHaveBeenCalled()
    expect(api.getCommitCredit).not.toHaveBeenCalled()
    expect(api.getBranchProtection).not.toHaveBeenCalled()
    expect(api.listProjectRoles).not.toHaveBeenCalled()
    expect(api.getProjectCreditBudget).not.toHaveBeenCalled()
    expect(api.getProjectDefaultModel).toHaveBeenCalledTimes(1)
  })
})
