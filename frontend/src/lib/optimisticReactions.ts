// 表情回应的乐观更新：点下去先把记号挪到眼前，再等服务器点头。
//
// 服务器给的那份聚合永远是**全量的一整份**（HTTP 响应和 `reaction` WS 帧带的是同一
// 份），所以「先乐观、后覆盖」不会把同一格数两遍——只要本地算出来的和服务器算的是
// 同一件事。`toggleReactionAgg` 就是那件事，和后端 `reactions_for_blocks` 一个算
// 法：新表情追加在末尾、我不在就加到 authors 末尾、我已经在就撤掉、撤空了整组消失。
import type { ReactionAgg } from '../cx_types'

/** 我（`me`）在 `reactions` 上点一下 `emoji` 之后的聚合——和服务器会算的相同。 */
export function toggleReactionAgg(reactions: ReactionAgg[] | undefined, emoji: string, me: string): ReactionAgg[] {
  const list = reactions ?? []
  const i = list.findIndex((r) => r.emoji === emoji)
  if (i === -1) return [...list, { emoji, count: 1, authors: [me] }]
  const cur = list[i]
  const authors = cur.authors.includes(me) ? cur.authors.filter((a) => a !== me) : [...cur.authors, me]
  // 撤空的组整个消失，和服务器一致（没有 count:0 的格子）。
  if (authors.length === 0) return list.filter((_, j) => j !== i)
  const next = list.slice()
  next[i] = { emoji, count: authors.length, authors }
  return next
}

export interface ReactionToggleDeps {
  /** 这一刻这一块的反应（乐观改过之后读到的就是乐观值）。 */
  current: () => ReactionAgg[] | undefined
  /** 把一份聚合写到这一块上。 */
  apply: (next: ReactionAgg[]) => void
  /** 发请求；返回值是服务器算的全量聚合。 */
  toggle: (emoji: string) => Promise<ReactionAgg[]>
  /** 失败时收尾（弹当前错误文案之类）。 */
  fail: (e: unknown) => void
}

/**
 * 乐观地翻一格记号：先本地算下一份聚合改上去，请求成功用服务器的全量聚合覆盖，失败
 * 退回点击前的样子。
 *
 * 「不会数两遍」是这份全量语义给的：乐观值和服务器值是同一份，随后到的 WS 帧也是同
 * 一份，谁先到都是整份替换，而不是在本地加减。
 */
export async function runReactionToggle(emoji: string, me: string, deps: ReactionToggleDeps): Promise<void> {
  const before = deps.current() ?? []
  deps.apply(toggleReactionAgg(before, emoji, me))
  try {
    deps.apply(await deps.toggle(emoji))
  } catch (e) {
    deps.apply(before)
    deps.fail(e)
  }
}
