// 房间当前预览的指针：芝士最后摆出来的那一样（`cheese show` / `cheese serve`）。
//
// 「预览」那一格要等它才知道自己指着哪份文件，而那一格本身要等话题数据加一串懒
// 加载 chunk 才挂得上来——实测冷开一个房间，这份请求要 9 秒才发得出。可指针只要
// 一个话题 id 就能问，而那个 id 在路由一解析出来就有。所以这里像 `blockCache` 一
// 样，让路由守卫先起头：chunk 还在路上、话题数据还没回来，这个答案已经先出发了。
// 面板挂上来时先读这份缓存，命中就不必再等一轮网络。
//
// 同一个话题最多一条在飞：守卫、面板挂载、窗口回到前台可能在同一秒里都想要它，
// 而它们要的是同一样东西。取回来的那份留着，下一次开同一个房间先有。
import type { PreviewInfo } from '../cx_types'

import { getPreview } from '../api'

// 取过就有（文件或 null=这个房间还没有预览），没取过是 undefined。
const cache = new Map<string, PreviewInfo | null>()
const inFlight = new Map<string, Promise<PreviewInfo | null>>()

/** 这个房间的当前预览，取过就有；没取过是 undefined，取过而没有是 null。 */
export function cachedPreviewPointer(topicId: string): PreviewInfo | null | undefined {
  return cache.has(topicId) ? cache.get(topicId) : undefined
}

/** 面板自己取回来的那一份也写回来，下一次开同一个房间就先有。 */
export function setPreviewPointer(topicId: string, art: PreviewInfo | null): void {
  cache.set(topicId, art)
}

/**
 * 顺手取一次这个房间的当前预览。同一个话题复用正在飞的那一条。
 *
 * 失败不写缓存、也不吞掉：调用方分得出「没有预览」和「没问成」——路由守卫只要顺
 * 手（失败没人看得见），挂载那一步要拿它当状态（问不成时保持原样，不清掉提示）。
 */
export function refreshPreviewPointer(topicId: string): Promise<PreviewInfo | null> {
  const running = inFlight.get(topicId)
  if (running) return running
  const started = getPreview(topicId)
    .then((art) => {
      const value = art ?? null
      setPreviewPointer(topicId, value)
      return value
    })
    .finally(() => inFlight.delete(topicId))
  inFlight.set(topicId, started)
  return started
}

/**
 * 首屏要的那一份答案，如果它已经在手边：正在飞的那一条（守卫可能刚起头），或者取过
 * 留着的那一份。都没有就是 `undefined`，由调用方自己决定要不要现问。
 *
 * 它只读、不写：这里要的是「别再压一轮网络在渲染前面」，不是「把这次的结果记下来」
 * ——要记下来的（守卫、轮询、收工重取）走 `refreshPreviewPointer`。面板首屏那份答案
 * 于是不会反过来喂大这份缓存；缓存里留着的一直是「问过它的那几处」留下的。
 */
export function warmPreviewPointer(topicId: string): Promise<PreviewInfo | null> | undefined {
  const running = inFlight.get(topicId)
  if (running) return running
  if (cache.has(topicId)) return Promise.resolve(cache.get(topicId) ?? null)
  return undefined
}

/** 测完一个用例把这份记忆擦干净：同一个进程里两段测试之间它不该带过去。 */
export function resetPreviewPointerCache(): void {
  cache.clear()
  inFlight.clear()
}
