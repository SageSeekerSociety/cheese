// 一条事件的作者：参与者，还是平台自己。
//
// `author_type` 曾经分三档（human / ai / system），前两档其实是同一件事：有人说
// 了一句话。人和 AI 队友是同一种参与者，区别只在**是哪一个**——而一个房间里坐得
// 下好几个人和好几个队友，一个三档枚举说不出是哪一个。所以这一列只剩两档，「这
// 句是不是芝士说的」改问署名（`author`，一条 handle）。
//
// 署名的写法和后端 `app/domain/identity/handles.py` 逐字一致：平台身份 `cheese`，
// 每个实例 `cheese-<hex>`。名册到手时用名册（`TopicMemberRow.agent` 更准，能认出
// 换了名字的队友）；名册还没到就靠这条命名规则。

import type { Block } from '@/cx_types'

const CHEESE_HANDLE = 'cheese'
const AGENT_HANDLE_PREFIX = 'cheese-'

/** 这个 handle 是不是芝士的（本体或某个实例）。 */
export function isAgentHandle(handle: string): boolean {
  return handle === CHEESE_HANDLE || handle.startsWith(AGENT_HANDLE_PREFIX)
}

/** 这条是平台自己写的（部署提醒、闸门结论、自动重发），不是谁说的话。 */
export function isPlatformBlock(b: Pick<Block, 'author_type'>): boolean {
  return b.author_type === 'system'
}

/** 这条是芝士说的话。 */
export function isAgentBlock(b: Pick<Block, 'author_type' | 'author'>): boolean {
  return !isPlatformBlock(b) && isAgentHandle(b.author)
}

/** 这条是一个人说的话。 */
export function isPersonBlock(b: Pick<Block, 'author_type' | 'author'>): boolean {
  return !isPlatformBlock(b) && !isAgentHandle(b.author)
}
