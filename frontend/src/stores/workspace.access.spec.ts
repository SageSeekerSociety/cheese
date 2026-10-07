/** 「进不来」要留成一个状态，不能只闪一条红条。
 *
 * 非成员打开项目链接时，话题列表答 401（没登录）或 403（不是成员）。这个错误过去
 * 只经 `reportError` 变成一条 4 秒的 snackbar，之后界面上什么都不剩——话题列表
 * 空白、项目名不显示，跟一个刚建好的空项目无法区分。
 *
 * 这里断言的是那两档被记住了，而且**只有**这两档被记住：一次网络抖动被写成
 * 「你没有权限」，比什么都不说更糟。其余读失败（5xx／断网）写进 `topicsError`，
 * 由侧栏就地显示 + 重试（docs/design-system.md §3.10）。
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listTopics = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listTopics: (...a: unknown[]) => listTopics(...a),
    listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
    listProjects: vi.fn().mockResolvedValue({ data: [] }),
    getTopicNotifyLevels: vi.fn().mockResolvedValue({}),
    getTopicUnread: vi.fn().mockResolvedValue({}),
    getPrivateUnread: vi.fn().mockResolvedValue({}),
  }
})

import { ApiError } from '@/api'
import { useWorkspaceStore } from '@/stores/workspace'

beforeEach(() => {
  setActivePinia(createPinia())
  listTopics.mockReset()
})

async function open(failWith: unknown, project = 'p1') {
  listTopics.mockRejectedValue(failWith)
  const store = useWorkspaceStore()
  await store.openProject(project)
  return store
}

describe('打不开一个项目的时候', () => {
  it('没登录：记成「要登录」', async () => {
    const store = await open(new ApiError(401, 'Login required to access a project'))
    expect(store.accessDenied).toBe('unauthenticated')
  })

  it('登录了但不是成员：记成「不是成员」', async () => {
    const store = await open(new ApiError(403, '你不是这个项目的成员，无权查看'))
    expect(store.accessDenied).toBe('forbidden')
  })

  // 这两档换成的是整块内容区的一段说明，所以不能同时再弹一条红条——同一件事说
  // 两遍，其中一遍还会自己消失。
  it('这两档不再走那条会消失的红条', async () => {
    const store = await open(new ApiError(403, '你不是这个项目的成员，无权查看'))
    expect(store.error).toBeNull()
  })

  // 500、断网、超时都不是「你没有权限」。写错了比不写更糟：它会让一个本来该重试
  // 的人以为自己被拒之门外。这些是「话题清单没读到」，写进 `topicsError` 就地报错
  // + 重试（不是那条会消失、之后和「暂无话题」分不出来的红条）。
  it.each([
    ['服务端出错', new ApiError(500, 'boom')],
    ['网络断了', new TypeError('Failed to fetch')],
  ])('%s 不算没有权限，写成侧栏那块就地报错', async (_what, failure) => {
    const store = await open(failure)
    expect(store.accessDenied).toBeNull()
    expect(store.topicsError).not.toBeNull()
    expect(store.error).toBeNull()
  })

  // 「没权限」给的是整块说明，不是那条能重试的就地报错——拿重试当答案会把一个
  // 根本不该重试的人一直按在同一处。
  it('没权限时不给就地报错（也就没有重试）', async () => {
    const store = await open(new ApiError(403, '你不是这个项目的成员，无权查看'))
    expect(store.topicsError).toBeNull()
  })

  // 深链接被拦下 → 登录 → 回到同一个项目：这时的「重新进入」不是从首页回来，
  // 手里没有旧的树可保留，说明必须撤掉、内容必须真的去取。
  it('登录后回到同一个项目，说明撤掉、内容取回来', async () => {
    const store = await open(new ApiError(401, 'Login required to access a project'))
    listTopics.mockResolvedValue({ data: [{ id: 't1', kind: 'root' }] })
    await store.openProject('p1')
    expect(store.accessDenied).toBeNull()
    expect(store.topics.map((t) => t.id)).toEqual(['t1'])
  })

  it('换一个进得去的项目，说明就撤掉', async () => {
    const store = await open(new ApiError(403, '你不是这个项目的成员，无权查看'))
    listTopics.mockResolvedValue({ data: [] })
    await store.openProject('p2')
    expect(store.accessDenied).toBeNull()
  })
})

describe('开着的项目页，登录在中途没了', () => {
  async function openFine() {
    listTopics.mockResolvedValue({ data: [{ id: 't1', kind: 'root' }] })
    const store = useWorkspaceStore()
    await store.openProject('p1')
    return store
  }

  // 侧栏每 30 秒在后台重读一次话题清单。登录没了之后那一次答 401，侧栏要是
  // 照旧摆着上一份，内容区里的看板就只剩一句「加载失败」加一个永远重试不好的按钮。
  it('后台那次重读答 401：整页换成「要登录」', async () => {
    const store = await openFine()
    listTopics.mockRejectedValue(new ApiError(401, 'Sign in to open this project'))
    await store.refreshTopics()
    expect(store.accessDenied).toBe('unauthenticated')
  })

  it('后台那次重读断网：什么都不换，下一次再读', async () => {
    const store = await openFine()
    listTopics.mockRejectedValue(new TypeError('Failed to fetch'))
    await store.refreshTopics()
    expect(store.accessDenied).toBeNull()
    expect(store.topics.map((t) => t.id)).toEqual(['t1'])
  })
})
