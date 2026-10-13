// 换人登录必须丢掉上一个人的缓存。
//
// service worker 的 API 读缓存（vite.config.ts 的 `cheese-api-get`）按 URL 建键：
// 请求头不进键，后端也没发 `Vary`，所以 `GET /api/projects` 全浏览器只有一份。服务器
// 数据的缓存（query/client）住在内存里，项目清单还在本标签页存了一份，同样不按人
// 分。危险的那一下不是退出，是换人登录 —— 上一个人关掉标签页就走了、没点退出，下一个
// 人登进来时它们都还在，上一个人的项目名会先画到这个人屏幕上。
//
// 反过来也要钉住：续签令牌走的也是登录那条路，每小时清一次等于这些缓存从来不存在。
// 所以判据是身份变了，不是又登了一次。
import type { Project } from '@/cx_types'

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { dropCachesIfSomeoneElseLogsIn } from './account'

import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { persistProjectList } from '@/query/persist'
import { seedProjects } from '@/test/seedQueries'

const del = vi.fn(() => Promise.resolve(true))
const theirs = { id: 'p-a', name: '上一个人的项目' } as Project

beforeEach(() => {
  del.mockClear()
  vi.stubGlobal('caches', { delete: del })
  seedProjects([theirs])
})

const held = () => queryClient.getQueryData<Project[]>(keys.projects())

describe('换人登录与上一个人的缓存', () => {
  it('上一个人读到的东西都不许留给下一个人', () => {
    expect(dropCachesIfSomeoneElseLogsIn(7, 9)).toBe(true)
    expect(del).toHaveBeenCalledWith('cheese-api-get')
    expect(held()).toBeUndefined()
  })

  it('同一个人续签令牌不清 —— 否则这些缓存等于不存在', () => {
    expect(dropCachesIfSomeoneElseLogsIn(7, 7)).toBe(false)
    expect(del).not.toHaveBeenCalled()
    expect(held()).toEqual([theirs])
  })

  it('认不出新身份时当作换了人', () => {
    // OAuth 回调只拿到令牌，用户信息随后才拉 —— 那条路径只在全新登录里走到。
    expect(dropCachesIfSomeoneElseLogsIn(7, undefined)).toBe(true)
    expect(del).toHaveBeenCalledWith('cheese-api-get')
    expect(held()).toBeUndefined()
  })

  it('本来就没人登录过也清一次 —— 缓存可能是上一个会话留下的', () => {
    expect(dropCachesIfSomeoneElseLogsIn(undefined, 9)).toBe(true)
    expect(held()).toBeUndefined()
  })

  it('本标签页存着的那份项目清单也清掉：刷新页面不会把它放回来', async () => {
    const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
    // 打开页面时那一步：之后清单一变就存一份。它订阅的是模块里那一个缓存，不退订，
    // 只活在这个文件里。
    persistProjectList()
    await tick()
    seedProjects([{ ...theirs }])
    await tick()
    const stored = () =>
      Array.from({ length: sessionStorage.length }, (_, i) => sessionStorage.getItem(sessionStorage.key(i) ?? '') ?? '')
    expect(stored().some((value) => value.includes(theirs.name))).toBe(true)

    dropCachesIfSomeoneElseLogsIn(7, 9)
    await tick()

    expect(stored().some((value) => value.includes(theirs.name))).toBe(false)
  })

  it('换人之前发出去的读晚回来，也落不进下一个人的缓存', async () => {
    let answer!: (list: Project[]) => void
    const before = queryClient.fetchQuery({
      queryKey: keys.projects(),
      queryFn: () => new Promise<Project[]>((resolve) => (answer = resolve)),
      staleTime: 0,
    })
    dropCachesIfSomeoneElseLogsIn(7, 9)
    answer([theirs])
    await before.catch(() => {})
    expect(held()).toBeUndefined()
  })

  it('没有 Cache Storage 的环境不炸，内存里那份照样清', () => {
    vi.stubGlobal('caches', undefined)
    expect(dropCachesIfSomeoneElseLogsIn(7, 9)).toBe(true)
    expect(held()).toBeUndefined()
  })
})
