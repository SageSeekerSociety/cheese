// 顶部「AI 现在在干什么」里的红色断开行：哪几位 AI 队友停在一次失败上、还没人回来接。
//
// 判据全在对话里，不另起一份状态：一条平台失败记录（`turn_failed` / `platform_error`，
// 严重度 error，后端 `hooks.py` 在它的 meta 里写了 `agent_handle`＝哪位的那一轮），之后这位
// 没有再开过口、也不在干活，就还没恢复。它再说话、或者有新一轮跑起来，红行就没了。

import type { Block } from '@/cx_types'

/** 一位停在失败上的队友。`code` 是后端给这次失败的分类（`login_expired`、`model_error`…）。 */
export interface AgentFailureLine {
  handle: string
  name: string
  code: string | null
  /** 失败落在对话里的时刻（ISO）。 */
  since: string
}

const FAILED_EVENTS = new Set(['turn_failed', 'platform_error'])

/** 这些原因下，红行有一个能直接做的动作：重新登录。 */
export function needsRelogin(line: Pick<AgentFailureLine, 'code'>): boolean {
  return line.code === 'login_expired'
}

/** 按时间从早到晚的对话里，每位队友最近一次还没恢复的失败。
 *  `working` 是此刻在干活（或排着队、等自动重试）的那几位的 handle：他们不算断开。 */
export function agentFailures(
  messages: readonly Block[],
  working: ReadonlySet<string>,
  nameOf: (handle: string) => string
): AgentFailureLine[] {
  const open = new Map<string, AgentFailureLine>()
  for (const block of messages) {
    const meta = block.meta
    const failed = !!meta && FAILED_EVENTS.has(String(meta.event_type ?? '')) && meta.severity === 'error'
    const handle = failed ? String(meta?.agent_handle ?? '') : ''
    if (failed && handle) {
      open.set(handle, {
        handle,
        name: nameOf(handle),
        code: typeof meta?.code === 'string' ? meta.code : null,
        since: block.created_at,
      })
      continue
    }
    // 这位后来又开口了：那次失败已经过去。
    if (open.has(block.author) && !failed) open.delete(block.author)
  }
  return [...open.values()].filter((line) => !working.has(line.handle))
}
