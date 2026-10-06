// 对话栏里和支线有关的那几件事，放在对话面板旁边而不是塞进它（useChatPanel 已经够长）：
//
// - 主线上一条消息下面那一行写「芝士 正在回复」：AI 队友此刻在那条支线里有一轮在跑。
//   读到页面时后端在那一行上带着（`replying`），之后每次它开始、停下，主线收到一帧
//   `thread_activity`。不靠猜：叫了它、过了多久，都说明不了它此刻在不在答。
// - 频道说它的支线变了（`threads`）：把屏幕上那几条消息下面那一行换成新的。
// - 那位队友在支线里排队、重试、整理上下文、等环境时，那一行写它在等什么：支线里的
//   运行记录同时告诉主线一份（`thread_status`），这里留每条支线最近那一条。
// - 支线里我上一句叫过 AI 队友，打开时输入框先带上「@芝士 」，可以删。
import type { Ref } from 'vue'
import type { Block } from '../cx_types'
import type { ThreadSummary } from '../types/threads'

import { reactive } from 'vue'

import { isQueued, threadWaitLabel, WAITS } from '../lib/threadStatus'

import { getThread, listThreads } from '@/api/threads'

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
  /** 队友叫什么。 */
  agentNameOf: (handle: string | undefined) => string
}) {
  // 支线 id → 此刻在里面回答的队友。收到过一帧的支线以这里为准，盖过读页面时的那一份。
  const live = reactive(new Map<string, string[]>())
  // 支线 id → 里面最近那一条说得出在等什么的运行记录。
  const waits = reactive(new Map<string, Block>())

  function answering(thread: ThreadSummary): string[] {
    return live.get(thread.id) ?? thread.replying ?? []
  }

  /** 这条消息下面那一行要写谁正在回复；没有人就是 null。 */
  function replyingFor(m: Block): string | null {
    if (!opts.mainLine() || !m.thread) return null
    const who = answering(m.thread)
    if (who.length) return opts.agentNameOf(who[0])
    // 还没开始、在排队的那一轮：它的队友也算在回复，只是还在等。
    const queued = waits.get(m.thread.id)
    const seat = queued && isQueued(queued) ? queued.meta?.seat : null
    return typeof seat === 'string' ? opts.agentNameOf(seat) : null
  }

  /** 那位队友此刻在等什么（排队、重试、整理上下文、等环境）；说不出就是 null。 */
  function statusFor(m: Block): string | null {
    if (!opts.mainLine() || !m.thread) return null
    const record = waits.get(m.thread.id)
    return record ? threadWaitLabel(record) : null
  }

  /** 支线里记下了一条运行记录。排队那一条来的时候屏幕上可能还没有那一行，补上。 */
  function onStatus(threadId: string, record: Block) {
    if (!WAITS.has(String(record.meta?.event_type ?? ''))) return
    waits.set(threadId, record)
    if (isQueued(record)) void ensureLine(threadId)
  }

  /** 一位队友在一条支线里开始或停下回答。屏幕上还没有那一行（刚 @ 了它、支线里还
   * 没有回复）时，问一次支线挂在哪条消息上，补上那一行。 */
  async function onActivity(threadId: string, member: string, active: boolean) {
    const now = live.get(threadId) ?? []
    live.set(threadId, active ? [...new Set([...now, member])] : now.filter((h) => h !== member))
    // 开始了就不在排队；停下来就什么都不等了。
    const waiting = waits.get(threadId)
    if (!active || (waiting && isQueued(waiting))) waits.delete(threadId)
    // 停下来时还没有回复：多半是这一轮出错了，再读一次支线，那一行才说得出「回复失败」。
    if (!active) {
      const shown = opts.timeline.messages.value.find((m) => m.thread?.id === threadId)
      if (shown && !shown.thread?.reply_count) void refresh()
      return
    }
    await ensureLine(threadId)
  }

  async function ensureLine(threadId: string) {
    if (opts.timeline.messages.value.some((m) => m.thread?.id === threadId)) return
    try {
      const thread = await getThread(threadId)
      const shown = opts.timeline.find(thread.root_block_id)
      if (!shown || shown.thread) return
      opts.timeline.replace({
        ...shown,
        thread: {
          id: thread.id,
          room_id: thread.room_id,
          root_block_id: thread.root_block_id,
          reply_count: thread.reply_count,
          last_reply_at: thread.last_reply_at,
          last_reply: null,
          participants: [],
          task: null,
        },
      })
    } catch {
      // 那一行等频道下次说支线变了时再补。
    }
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
            participants: row.participants,
            task: row.task,
            failed: row.failed,
            replying: shown.thread?.replying,
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

  return { replyingFor, statusFor, onActivity, onStatus, refresh }
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
