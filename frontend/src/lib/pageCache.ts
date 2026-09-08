// 页面级取数缓存：一个 key 记一份「上一次成功拿到的东西」，只活在这个标签页的
// 内存里（刷新页面就没了，不落 storage —— 这里装的是项目数据，不是偏好）。
//
// 存在的理由是 blockCache.ts 已经在聊天上验证过的那一招：**先把上次的画出来，
// 再在背后重取**。聊天之外的页面（总览 / 文档 / 成员 / 日历 / AI 队友）过去每
// 一次进入都是「清空 → 转圈 → 请求 → 渲染」，所以第二次进和第一次进一样贵，
// 而人对「我刚看过的那一屏」是有记忆的，转圈只是在否认它。
//
// 这个文件只管两件不涉及 Vue 的事，界面那一半在 composables/useCachedResource.ts：
//   1. key → 上次的值，条目数有上限
//   2. 同一个 key 同一时刻只发一个请求，第二个调用者复用第一个的 promise

// 上限存在的理由是内存，不是命中率：key 里带着项目 id / 成员 handle / 文档
// kind，一个逛得久的会话能生出上百个不同的 key，而一份总览的 payload 不小。
// 50 条足够覆盖「刚才来回切的那几页」，那正是这层缓存唯一要救的场景。
const MAX_ENTRIES = 50

const entries = new Map<string, unknown>()
const inflight = new Map<string, Promise<unknown>>()

// 「现在这批数据属于谁」。clearPageCache() 让它 +1，于是那一刻还在飞的请求回来
// 之后认得出自己已经过期，不会把上一个人的数据重新写进一张刚清空的表。
let generation = 0

export function hasCachedPage(key: string): boolean {
  return entries.has(key)
}

export function readCachedPage<T>(key: string): T | undefined {
  return entries.get(key) as T | undefined
}

export function writeCachedPage<T>(key: string, value: T): void {
  // 先删再写：Map 的迭代顺序是首次插入的顺序，不删的话一个每次进页面都在刷新的
  // 热 key 会永远停在队头，第 51 个新 key 挤掉的就是它。
  entries.delete(key)
  entries.set(key, value)
  while (entries.size > MAX_ENTRIES) {
    const oldest = entries.keys().next()
    if (oldest.done) break
    entries.delete(oldest.value)
  }
}

/**
 * 清空所有页面缓存。退出登录时必须调用。
 *
 * 共用一台电脑时，上一个人的项目数据不能留在内存里被下一个人看到——所以连
 * 「正在飞的请求」也要一起作废：它带着上一个身份发出去，回来时这台机器可能已经
 * 是别人的了。
 */
export function clearPageCache(): void {
  generation += 1
  entries.clear()
  inflight.clear()
}

/**
 * 取一次数，并把结果写进缓存。同一个 key 同一时刻只会真的发一个请求——后来的
 * 调用者拿到的是同一个 promise。
 *
 * 失败不写缓存，也不留痕迹：下一次调用会重新发。
 */
export function fetchCachedPage<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const running = inflight.get(key) as Promise<T> | undefined
  if (running) return running

  const startedAt = generation
  const request = fetcher().then((value) => {
    // 这中间有人退出登录了：结果属于上一个身份，丢掉。
    if (startedAt === generation) writeCachedPage(key, value)
    return value
  })
  const tracked = request.finally(() => {
    // 只删自己那一条——clearPageCache 之后可能已经有新的一条排在这个 key 上了。
    if (inflight.get(key) === tracked) inflight.delete(key)
  })
  inflight.set(key, tracked)
  return tracked
}
