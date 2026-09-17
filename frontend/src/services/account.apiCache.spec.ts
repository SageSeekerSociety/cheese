// 换人登录必须丢掉上一个人的两份缓存。
//
// service worker 的 API 读缓存（vite.config.ts 的 `cheese-api-get`）按 URL 建键：
// 请求头不进键，后端也没发 `Vary`，所以 `GET /api/projects` 全浏览器只有一份。页面
// 缓存住在内存里，同样不按人分。两份本来都只在退出登录时清，而危险的那一下不是退
// 出，是换人登录 —— 上一个人关掉标签页就走了、没点退出，下一个人登进来时两份都还
// 在。API 那份是 NetworkFirst、五秒超时就回退，于是共用机器上一次慢请求会把上一个
// 人的数据画到这个人屏幕上。
//
// 反过来也要钉住：续签令牌走的也是 login()，每小时清一次等于这两份缓存从来不存在。
// 所以判据是身份变了，不是又登了一次。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const clearPageCache = vi.fn()

vi.mock('@/lib/pageCache', () => ({
  clearPageCache: () => clearPageCache(),
}))

const del = vi.fn(() => Promise.resolve(true))

let dropCachesIfSomeoneElseLogsIn: (a?: number, b?: number) => boolean

beforeEach(async () => {
  del.mockClear()
  clearPageCache.mockClear()
  vi.stubGlobal('caches', { delete: del })
  vi.resetModules()
  ;({ dropCachesIfSomeoneElseLogsIn } = await import('./account'))
})

describe('换人登录与上一个人的缓存', () => {
  it('上一个人的两份缓存都不许留给下一个人', () => {
    expect(dropCachesIfSomeoneElseLogsIn(7, 9)).toBe(true)
    expect(del).toHaveBeenCalledWith('cheese-api-get')
    expect(clearPageCache).toHaveBeenCalled()
  })

  it('同一个人续签令牌不清 —— 否则这两份缓存等于不存在', () => {
    expect(dropCachesIfSomeoneElseLogsIn(7, 7)).toBe(false)
    expect(del).not.toHaveBeenCalled()
    expect(clearPageCache).not.toHaveBeenCalled()
  })

  it('认不出新身份时当作换了人', () => {
    // OAuth 回调只拿到令牌，用户信息随后才拉 —— 那条路径只在全新登录里走到。
    expect(dropCachesIfSomeoneElseLogsIn(7, undefined)).toBe(true)
    expect(del).toHaveBeenCalledWith('cheese-api-get')
  })

  it('本来就没人登录过也清一次 —— 缓存可能是上一个会话留下的', () => {
    expect(dropCachesIfSomeoneElseLogsIn(undefined, 9)).toBe(true)
  })

  it('没有 Cache Storage 的环境不炸，页面缓存照样清', () => {
    vi.stubGlobal('caches', undefined)
    expect(dropCachesIfSomeoneElseLogsIn(7, 9)).toBe(true)
    expect(clearPageCache).toHaveBeenCalled()
  })
})
