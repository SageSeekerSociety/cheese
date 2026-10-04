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
// 最多记 MAX_TOPICS 个房间，和工作面板那几份（lib/topicPanelCache.ts）同一个上限。
const MAX_TOPICS = 20
const cache = new Map<string, PreviewInfo | null>()
// 清空一次 +1：那一刻还在飞的请求回来后不写回来。
let generation = 0
const inFlight = new Map<string, Promise<PreviewInfo | null>>()

/** 这个房间的当前预览，取过就有；没取过是 undefined，取过而没有是 null。 */
export function cachedPreviewPointer(topicId: string): PreviewInfo | null | undefined {
  return cache.has(topicId) ? cache.get(topicId) : undefined
}

/** 面板自己取回来的那一份也写回来，下一次开同一个房间就先有。 */
export function setPreviewPointer(topicId: string, art: PreviewInfo | null): void {
  // 先删再写，最近写过的排到最后；超出上限丢最久没写过的那个房间。
  cache.delete(topicId)
  cache.set(topicId, art)
  while (cache.size > MAX_TOPICS) {
    const oldest = cache.keys().next()
    if (oldest.done) break
    cache.delete(oldest.value)
  }
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
  const startedAt = generation
  const started: Promise<PreviewInfo | null> = getPreview(topicId)
    .then((art) => {
      const value = art ?? null
      if (startedAt === generation) setPreviewPointer(topicId, value)
      return value
    })
    .finally(() => {
      // 清空过之后可能已经有新的一条排在这个话题上，只删自己那条。
      if (inFlight.get(topicId) === started) inFlight.delete(topicId)
    })
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

/** 退出登录时、以及测完一个用例时把这份记忆擦干净：上一个人（上一段测试）的不该带过去。 */
export function resetPreviewPointerCache(): void {
  generation += 1
  cache.clear()
  inFlight.clear()
}
