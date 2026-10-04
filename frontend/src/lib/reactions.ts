// 表情回应（Slack 语义）的乐观更新。
//
// 点一下到这一格亮起来之间，隔着整整一次往返——非缓存的写请求在 4x CPU 上要几百
// 毫秒，而「点了没反应」正是这一处最容易感觉到的卡。所以先按「我」的记号本地翻出
// 一份新的聚合画上去，再发请求：
//
//   - 成功：服务端回来的那份是权威，直接覆盖（`count`、`authors` 都以它为准）。
//   - 失败：回滚到发请求前那一份——**但只在期间没有更新的聚合落进来时**。`reaction`
//     WS 帧（以及别人的回应）带的是服务端的事实，不能被我们手里那份过期的快照盖掉，
//     否则一次回声就会被回滚抹掉。帧里是这个块的**整份**聚合，所以期间哪怕只是别人
//     点了同一个块，跳过回滚也是对的。
import type { ReactionAgg } from '../cx_types'

/**
 * 本地翻一下：`me` 在 `emoji` 上没记号就加上、有就去掉，人数跟着走；一格里一个记号
 * 都不剩（人走光了）就把这一格整个去掉。`me` 为空（登录信息还没到）时原样返回——
 * 表态不了，就不假装表了态。
 *
 * 新开的一格排在末尾（服务端也按首次出现的先后排），成功后会被服务端那份覆盖。
 */
export function toggleReaction(reactions: ReactionAgg[], emoji: string, me: string | null): ReactionAgg[] {
  if (me === null) return reactions
  const next = reactions.map((r) => ({ ...r, authors: [...r.authors] }))
  const at = next.findIndex((r) => r.emoji === emoji)
  if (at < 0) return [...next, { emoji, count: 1, authors: [me] }]
  const authors = next[at].authors.includes(me)
    ? next[at].authors.filter((handle) => handle !== me)
    : [...next[at].authors, me]
  if (authors.length === 0) return next.filter((_, i) => i !== at)
  next[at] = { emoji, count: authors.length, authors }
  return next
}

/** 回滚要用到的接线：读现在屏上那一份、写、发请求、出错怎么办。 */
export interface ReactionToggleHooks {
  /** 此刻屏上那一份（拿它判断期间有没有更新的聚合落进来）。 */
  before: () => ReactionAgg[] | undefined
  apply: (blockId: string, reactions: ReactionAgg[]) => void
  send: (blockId: string, emoji: string) => Promise<{ reactions: ReactionAgg[] }>
  fail: (e: unknown) => void
}

/**
 * 先本地翻、再发请求；成功用服务端那份覆盖、失败回滚（条件见文件头）。
 *
 * `hooks.before()` 返回的是那一份数组的**引用**：`apply` 之后它就是屏上那一份，只要
 * 期间没有别的聚合写进来，它和本地算出的那份仍是同一个引用——用引用相等判断「有没有
 * 更新的聚合落进来」，不必逐格比对。
 */
export async function reactOptimistically(
  blockId: string,
  emoji: string,
  me: string | null,
  hooks: ReactionToggleHooks
): Promise<void> {
  const before = hooks.before() ?? []
  const optimistic = toggleReaction(before, emoji, me)
  hooks.apply(blockId, optimistic)
  try {
    hooks.apply(blockId, (await hooks.send(blockId, emoji)).reactions)
  } catch (e) {
    if (hooks.before() === optimistic) hooks.apply(blockId, before)
    hooks.fail(e)
  }
}
