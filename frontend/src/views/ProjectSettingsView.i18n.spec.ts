// 设置页的中英对照。githubSettingsSections / branchProtectionSection 两个 spec 扫的是
// 源码结构（哪一行绑了谁的 disabled），它们看不见「用户屏幕上有没有汉字」——那是渲染
// 出来的东西，只能挂起来看。这里补的就是这一层：整页英文下不许出现汉字。
//
// 子组件（运行环境、算力）各自有自己的翻译账，这里换成桩，免得把它们的欠账算到本页头上。
import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api'
import i18n, { setLocale } from '../i18n'

import ProjectSettingsView from './ProjectSettingsView.vue'

vi.mock('../api')
vi.mock('../components/ProjectEnvironmentSettings.vue', () => ({
  default: { template: '<section>ENV</section>' },
}))
vi.mock('../components/ProjectComputeSettings.vue', () => ({
  default: { template: '<section>COMPUTE</section>' },
}))
vi.mock('../me', () => ({ myHandle: () => 'alice', myId: () => 7 }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
}))

const CJK = /[㐀-䶿一-鿿豈-﫿]/

// 标上 api 的类型：不标的话 github_protection.status 会宽成 string，塞不进 mockResolvedValue。
const protections: Awaited<ReturnType<typeof api.getBranchProtection>> = {
  required_checks: [{ name: 'ci', paths: ['backend/**'] }],
  strict: true,
  dismiss_stale: true,
  auto_merge_allowed: false,
  override_handles: null,
  approvals_required: 2,
  default_reviewer: '',
  merge_method: 'squash',
  github_protection: { enforced: false, status: 'none' },
}

beforeEach(() => {
  // happy-dom 不给 devicePixelRatio，Vuetify 的浮层定位会真的去读。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.resetAllMocks()
  setLocale('zh-CN')
  vi.mocked(api.getProject).mockResolvedValue({ name: 'Example' } as Awaited<ReturnType<typeof api.getProject>>)
  vi.mocked(api.getUpstream).mockResolvedValue({ url: null })
  vi.mocked(api.getGithubConnection).mockResolvedValue({ connected: false })
  vi.mocked(api.getBranchProtection).mockResolvedValue({ ...protections })
  vi.mocked(api.listProjectMembers).mockResolvedValue({
    data: [{ user_handle: 'alice', name: 'Alice', agent: false }],
    total: 1,
  } as Awaited<ReturnType<typeof api.listProjectMembers>>)
  vi.mocked(api.listOAuthConnections).mockResolvedValue({ connections: [] })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/**
 * 挂载设置页，等 markers 全部出现，返回那一刻的整页文字。
 *
 * 抓快照，而不是「等到了再读一遍」：`loading` 的初值是 false，挂载后先渲染一版没有数据的
 * 骨架，onMounted 里的 load() 紧接着把它翻成加载中、再翻回来。中间那几轮会把区块内容整个
 * 换掉，等到了再读，很容易正好读到空档里的那一版。所以判定和取值放在同一次轮询里。
 *
 * markers 要挑数据到位才会出现的东西：骨架里就有的话（比如区块标题），等到了也不算数。
 */
async function mountSettings(...markers: string[]) {
  const element = document.createElement('div')
  const app = createApp(ProjectSettingsView, { projectId: 'project' })
  app.use(createVuetify())
  app.use(i18n)
  app.mount(element)
  let text = ''
  await vi.waitFor(() => {
    text = element.textContent ?? ''
    for (const marker of markers) expect(text, marker).toContain(marker)
  })
  return { text, unmount: () => app.unmount() }
}

describe('project settings in Chinese', () => {
  it('讲中文：区块标题和分支保护的规则项都在', async () => {
    const page = await mountSettings('项目设置 · Example', '暂无关联账号', '合并前必须通过的检查')
    try {
      expect(page.text).toContain('项目设置')
      expect(page.text).toContain('上游仓库')
      expect(page.text).toContain('分支保护')
      expect(page.text).toContain('任务默认 reviewer')
      expect(page.text).toContain('连接 GitHub 仓库')
      expect(page.text).toContain('连接 GitHub 账号')
    } finally {
      page.unmount()
    }
  })

  it('讲中文：设置本身加载失败时显示中文兜底文案', async () => {
    // 抛出来的不是 Error，才走页面自己那句兜底文案（Error 会直接显示 e.message）。
    vi.mocked(api.getProject).mockRejectedValue('boom')
    const page = await mountSettings('加载设置失败')
    try {
      // 显示的是词表里那句，不是抛出来的东西。
      expect(page.text).not.toContain('boom')
    } finally {
      page.unmount()
    }
  })
})

describe('project settings in English', () => {
  it('整个页面在英文下不留一个汉字', async () => {
    setLocale('en')
    const page = await mountSettings(
      'Project settings · Example',
      'No account connected',
      'Checks required before merging'
    )
    try {
      for (const line of [
        'Project settings',
        'Upstream repository',
        'Branch protection',
        'Checks required before merging',
        'Default task reviewer',
        'Connect a GitHub repository',
        'Connect a GitHub account',
      ]) {
        expect(page.text, line).toContain(line)
      }
      expect(CJK.test(page.text)).toBe(false)
    } finally {
      page.unmount()
    }
  })

  it('空态和「GitHub 已在执行」在英文下也是英文', async () => {
    setLocale('en')
    vi.mocked(api.getBranchProtection).mockResolvedValue({
      ...protections,
      required_checks: [],
      github_protection: { enforced: true, status: 'enforced' },
    })
    const page = await mountSettings('No required checks yet', 'GitHub is enforcing the rules below')
    try {
      expect(page.text).toContain('No required checks yet')
      expect(page.text).toContain('GitHub is enforcing the rules below')
      expect(page.text).toContain('No repository connected')
      expect(CJK.test(page.text)).toBe(false)
    } finally {
      page.unmount()
    }
  })

  it('设置加载失败时是英文兜底文案', async () => {
    setLocale('en')
    vi.mocked(api.getProject).mockRejectedValue('boom')
    const page = await mountSettings("Couldn't load the settings")
    try {
      expect(page.text).not.toContain('boom')
      expect(CJK.test(page.text)).toBe(false)
    } finally {
      page.unmount()
    }
  })

  it('两个区块各自加载失败时，各有一句英文文案和一个重试按钮', async () => {
    setLocale('en')
    vi.mocked(api.getBranchProtection).mockRejectedValue('boom')
    vi.mocked(api.listOAuthConnections).mockRejectedValue('boom')
    const page = await mountSettings("Couldn't load the branch protection rules", "Couldn't load the connection status")
    try {
      expect(page.text.match(/Try again/g)?.length, page.text).toBe(2)
      expect(CJK.test(page.text)).toBe(false)
    } finally {
      page.unmount()
    }
  })
})
