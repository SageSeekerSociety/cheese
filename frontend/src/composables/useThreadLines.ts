// 对话栏里和支线有关的那几件事，放在对话面板旁边而不是塞进它（useChatPanel 已经够长）：
//
// - 主线上一条消息叫了 AI 队友、它的支线里还没有回复时，那一行写「芝士 正在回复」：
//   回答不在主线上，人得知道去哪儿等。
// - 频道说它的支线变了（`threads`）：把屏幕上那几条消息下面那一行换成新的。
// - 支线里我上一句叫过 AI 队友，打开时输入框先带上「@芝士 」，可以删。
import type { Ref } from 'vue'
import type { Block } from '../cx_types'
import type { ThreadSummary } from '../types/threads'

import { listThreads } from '@/api/threads'

/** 叫了队友之后多久还算「正在回复」：过了这么久还没有一条回复，多半是这一轮没成。 */
const REPLYING_FOR_MS = 10 * 60 * 1000

interface Timeline {
  messages: Ref<Block[]>
  find: (id: string) => Block | undefined
  replace: (block: Block) => void
}

export function useThreadLines(opts: {
  /** 这一栏是频道的主线。 */
  mainLine: () => boolean
  roomId: () => string | null
  timeline: Timeline
  /** 被叫的队友叫什么。 */
  agentNameOf: (handle: string | undefined) => string
}) {
  function replyingFor(m: Block): string | null {
    if (!opts.mainLine() || m.kind !== 'message' || (m.thread?.reply_count ?? 0) > 0) return null
    const recipient = m.meta?.agent_recipient as { handle?: string; mentioned?: boolean } | undefined
    if (!recipient?.mentioned) return null
    if (Date.now() - Date.parse(m.created_at) > REPLYING_FOR_MS) return null
    return opts.agentNameOf(recipient.handle)
  }

  let refreshing: Promise<void> | null = null
  async function refresh() {
    const room = opts.roomId()
    if (!room || !opts.mainLine()) return
    if (refreshing) return refreshing
    refreshing = (async () => {
      try {
        const rows = await listThreads(room, 100)
        if (opts.roomId() !== room) return
        for (const row of rows) {
          const shown = opts.timeline.find(row.root_block_id)
          if (!shown) continue
          const summary: ThreadSummary = {
            id: row.id,
            room_id: row.room_id,
            root_block_id: row.root_block_id,
            reply_count: row.reply_count,
            last_reply_at: row.last_reply_at,
            last_reply: row.last_reply,
          }
          opts.timeline.replace({ ...shown, thread: summary })
        }
      } catch {
        // 下一次频道再说变了时再读。
      } finally {
        refreshing = null
      }
    })()
    return refreshing
  }

  return { replyingFor, refresh }
}

/** 支线里我上一句叫过 AI 队友：打开时输入框先带上的那几个字；否则空串。 */
export function summonPrefill(messages: Block[], isMine: (m: Block) => boolean, agentName: string): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.kind !== 'message' || !isMine(m)) continue
    const recipient = m.meta?.agent_recipient as { mentioned?: boolean } | undefined
    return recipient?.mentioned ? `@${agentName} ` : ''
  }
  return ''
}
