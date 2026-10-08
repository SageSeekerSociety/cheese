// Opening a topic waits on two things: the topic page's code and the topic's
// newest messages. The rule here is that the second does not wait for the
// first — the messages are already being fetched while the page code is still
// on its way. The page modules below never finish loading, which is exactly the
// moment the rule is about.
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'

const { signedIn, pageCode, release } = vi.hoisted(() => {
  // 页面代码在整个文件里都还在路上；文件跑完才放行（换成空组件），不留一个永远
  // 挂着的加载让测试环境拆掉之后还在后台读模块。
  let open!: () => void
  const gate = new Promise<void>((resolve) => (open = resolve))
  return {
    signedIn: { id: '7' },
    pageCode: () => gate.then(() => ({ default: { render: () => null } })),
    release: () => open(),
  }
})
vi.mock('@/me', () => ({ myId: () => signedIn.id, myHandle: () => (signedIn.id ? 'alice' : '') }))
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  listBlocks: vi.fn().mockResolvedValue({ data: [], has_more: false }),
  getPreview: vi.fn().mockResolvedValue(null),
}))
// 地址换短名要问后端；这里没有后端，换不成就照原地址打开。
vi.mock('@/api/addresses', () => ({
  resolveProject: vi.fn().mockRejectedValue(new Error('offline')),
  resolveNumber: vi.fn().mockRejectedValue(new Error('offline')),
  addressOf: vi.fn().mockRejectedValue(new Error('offline')),
}))
vi.mock('@/views/workspace/ProjectShell.vue', pageCode)
vi.mock('@/views/workspace/ProjectSidebar.vue', pageCode)
vi.mock('@/views/workspace/TopicView.vue', pageCode)

import { getPreview, listBlocks } from '@/api'
import { blockCache, setCachedWindow } from '@/lib/blockCache'
import { resetPreviewPointerCache } from '@/lib/previewPointer'
import router from '@/router'

const PROJECT = '3f1a7c62-9d4e-4b8a-8f21-0c5d6e7a9b10'

afterAll(async () => {
  release()
  await router.isReady().catch(() => {})
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(listBlocks).mockClear()
  vi.mocked(getPreview).mockClear()
  blockCache.clear()
  resetPreviewPointerCache()
  signedIn.id = '7'
})

describe('opening a topic', () => {
  // 这是这个文件里第一次导航：路由守卫要现编出它们 import 的那一整条模块图（账号、
  // 邮箱校验……），本地要一秒半，比 waitFor 默认的一秒长。超时红在这里说的是机器有
  // 多忙，不是消息有没有提前取，所以等待按「编译要多久」给（同 workspaceRoutes.spec.ts）。
  it('fetches its newest messages while the page code is still loading', async () => {
    const topic = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b01'
    void router.push(`/projects/${PROJECT}/channels/${topic}`)
    await vi.waitFor(() => expect(listBlocks).toHaveBeenCalledWith(topic, expect.anything()), { timeout: 20_000 })
  }, 30_000)

  it('fetches nothing for a topic whose messages are already on hand', async () => {
    const topic = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b02'
    setCachedWindow(topic, { blocks: [], hasMore: false })
    void router.push(`/projects/${PROJECT}/channels/${topic}`)
    await new Promise((r) => setTimeout(r, 20))
    expect(listBlocks).not.toHaveBeenCalled()
  })

  it('fetches nothing when nobody is signed in', async () => {
    signedIn.id = ''
    const topic = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b03'
    void router.push(`/projects/${PROJECT}/channels/${topic}`)
    await new Promise((r) => setTimeout(r, 20))
    expect(listBlocks).not.toHaveBeenCalled()
  })

  // 频道没有「预览」那一格（那是任务的，见 WorkPanel 的 CHANNEL_TABS）：打开频道
  // 不替它问「当前预览是哪一份」，问了也没有地方用。
  it('asks a channel for no preview', async () => {
    const topic = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b04'
    void router.push(`/projects/${PROJECT}/channels/${topic}`)
    await vi.waitFor(() => expect(listBlocks).toHaveBeenCalledWith(topic, expect.anything()), { timeout: 20_000 })
    expect(getPreview).not.toHaveBeenCalled()
  }, 30_000)

  // 任务的「预览」那一格要等任务数据加一串 chunk 才挂得上，而它一挂上就要这份答案
  // （实测冷开要 9 秒才发得出）。所以守卫替它先出发。
  it('asks a task for its preview while the page code is still loading', async () => {
    const task = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b06'
    void router.push(`/projects/${PROJECT}/tasks/${task}`)
    await vi.waitFor(() => expect(getPreview).toHaveBeenCalledWith(task), { timeout: 20_000 })
  }, 30_000)

  it('asks a task for no preview when nobody is signed in', async () => {
    signedIn.id = ''
    const task = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b07'
    void router.push(`/projects/${PROJECT}/tasks/${task}`)
    await new Promise((r) => setTimeout(r, 20))
    expect(getPreview).not.toHaveBeenCalled()
  })
})
