// 平台托管 → GitHub 的迁移：设置页那一块按钮背后的流程。迁完换成 GitHub 的仓库
// 状态；GitHub 那边还没装上 App 时去授权页；授权回跳 `?github_install=migrate`
// 时自己再要一次迁移，不让人再点一遍。
import type { ForgeConnection } from '@/cx_types'

import { defineComponent, h, nextTick } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useProjectSettings } from './useProjectSettings'

import * as api from '@/api'
import * as desktop from '@/lib/desktopApp'

vi.mock('@/api')
vi.mock('@/lib/desktopApp', () => ({ goAuthorize: vi.fn(() => false) }))
const query: Record<string, string> = {}
const replace = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ query }),
  useRouter: () => ({ replace }),
}))

const github: ForgeConnection = { kind: 'github_app', connected: true, repo: 'alice/homework' } as ForgeConnection

function host() {
  let state!: ReturnType<typeof useProjectSettings>
  render(
    defineComponent({
      setup() {
        state = useProjectSettings(() => 'p1')
        return () => h('div')
      },
    })
  )
  return state
}
const flush = async () => {
  for (let i = 0; i < 4; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  for (const key of Object.keys(query)) delete query[key]
  vi.mocked(api.getForgeConnection).mockResolvedValue(github)
})
afterEach(cleanup)

it('binds the page to GitHub once the move answers', async () => {
  vi.mocked(api.migrateToGithub).mockResolvedValue({ connected: true, repo: 'alice/homework' })
  const state = host()
  state.migrateUrl.value = ' https://github.com/alice/homework '
  await state.migrateToGithub()
  expect(api.migrateToGithub).toHaveBeenCalledWith('p1', 'https://github.com/alice/homework')
  expect(state.forgeConnection.value).toEqual(github)
  expect(state.githubRepoNotice.value?.type).toBe('success')
  expect(state.githubRepoNotice.value?.text).toContain('alice/homework')
  expect(state.connectingGithubRepo.value).toBe(false)
})

it('goes to GitHub to install the App when it cannot reach the repository yet', async () => {
  vi.mocked(api.migrateToGithub).mockResolvedValue({ connected: false, install_url: 'https://github.com/apps/x' })
  const state = host()
  state.migrateUrl.value = 'https://github.com/alice/homework'
  await state.migrateToGithub()
  expect(desktop.goAuthorize).toHaveBeenCalledWith('https://github.com/apps/x')
  expect(api.getForgeConnection).not.toHaveBeenCalled()
  // The page is leaving: the button keeps spinning until it does.
  expect(state.connectingGithubRepo.value).toBe(true)
})

it('shows a refusal where the move was asked for', async () => {
  vi.mocked(api.migrateToGithub).mockRejectedValue(new Error('这个 GitHub 仓库里已经有别的内容'))
  const state = host()
  state.migrateUrl.value = 'https://github.com/alice/homework'
  await state.migrateToGithub()
  expect(state.githubRepoNotice.value).toEqual({ type: 'error', text: '这个 GitHub 仓库里已经有别的内容' })
  expect(state.connectingGithubRepo.value).toBe(false)
})

it('asks for the move again when the installation lands back here', async () => {
  vi.mocked(api.migrateToGithub).mockResolvedValue({ connected: true, repo: 'alice/homework' })
  query.github_install = 'migrate'
  query.repo = 'alice/homework'
  const state = host()
  state.consumeGithubCallbackNotice()
  await flush()
  expect(api.migrateToGithub).toHaveBeenCalledWith('p1', 'https://github.com/alice/homework')
  expect(replace).toHaveBeenCalledWith({ query: {} })
  expect(state.githubRepoNotice.value?.type).toBe('success')
})
