/**
 * 发件箱：**已经打出去、但库里还没有**的那几条消息。
 *
 * 「人发的消息立即显示，绝不排在 AI turn 后面。别的都可以错，现场不能错」——
 * 所以消息先进这里、立刻出现在时间线末尾，再一条一条 POST 给后端
 * （`/topics/{id}/messages`，人和 AI 队友走的是同一扇门）。POST 的回答就是落库的那
 * 一条：拿到它，本地这条就换成真的。房间 socket 上的回声和重连后读回的历史也能对上
 * 账（后端把 `request_id` 原样戳在块的 `meta.client_id` 上），谁先到用谁。
 *
 * 一次只发一条，按打出去的顺序：前一条没落库，后一条不走，房间里的先后就是你打字
 * 的先后。连不上（断网、后端在发版、请求超时）不算失败——这一条回到队列，过一会儿
 * 带**同一个** `request_id` 再发，后端认得它，不会落两遍。后端明确拒了的那条才算
 * 失败：它变成一条能重试、能拿回去改的行——安静地丢掉一句话，比显示一条「未送达」
 * 糟得多。
 *
 * 这里不认识话题，也不认识轮次。「发出去之后房间该有什么反应」（等回复的指示、
 * 滚到底、清掉回复目标）是房间壳的事。
 */

import type { Block } from '../../../cx_types'
import type { Outgoing } from '../../../lib/composerDrafts'

import { onScopeDispose, ref } from 'vue'

/**
 * 一次发送等多久算没送到。宁可长一点：误报比晚一点显示更伤——房间里已经有过一次
 * 这种误报（#539）。等满了也不判失败，只是带同一个 id 再发一次。
 */
const SEND_TIMEOUT_MS = 30_000

/** 连不上时，下一次再试之前等多久（逐次加长，封顶）。 */
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 15_000]

/**
 * 后端说了「不」（`send` 抛它）：原样再发也还是「不」，要人来决定。别的失败都当成这一刻
 * 的链路问题，带同一个 id 再发一次就可能成。
 */
export class SendRefused extends Error {}

export function useOutbox(options: {
  /** POST 这一条；兑现为落库的那一块。后端拒了就抛 `SendRefused`。 */
  send: (message: Outgoing, signal: AbortSignal) => Promise<Block>
  /** 落库的那一块回来了：画到时间线上（socket 上可能也会再来一遍，由时间线去重）。 */
  onDelivered: (block: Block) => void
}) {
  const outbox = ref<Outgoing[]>([])
  let running = false
  let inFlight: AbortController | null = null
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let retryAttempt = 0

  function cancelRetry() {
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = null
  }

  function scheduleRetry() {
    if (retryTimer) return
    const delay = RETRY_DELAYS_MS[Math.min(retryAttempt, RETRY_DELAYS_MS.length - 1)]
    retryAttempt += 1
    retryTimer = setTimeout(() => {
      retryTimer = null
      void flush()
    }, delay)
  }

  /** 停下手上这一次发送和等着的重试（切话题、卸载）。没送完的那条回到队列。 */
  function pause() {
    cancelRetry()
    inFlight?.abort()
  }

  async function sendOne(item: Outgoing): Promise<'next' | 'stop'> {
    item.state = 'sending'
    item.error = undefined
    const controller = new AbortController()
    inFlight = controller
    const timer = setTimeout(() => controller.abort(), SEND_TIMEOUT_MS)
    try {
      const block = await options.send(item, controller.signal)
      retryAttempt = 0
      outbox.value = outbox.value.filter((o) => o.clientId !== item.clientId)
      options.onDelivered(block)
      return 'next'
    } catch (error) {
      if (!outbox.value.some((o) => o.clientId === item.clientId)) return 'next'
      if (error instanceof SendRefused) {
        item.state = 'failed'
        item.error = error.message
        return 'next'
      }
      // 链路问题（含超时、切走时被叫停）：回到队列，同一个 id 过一会儿再发。
      item.state = 'queued'
      return 'stop'
    } finally {
      clearTimeout(timer)
      if (inFlight === controller) inFlight = null
    }
  }

  /** 把排着的那几条按顺序发出去。已经在发就不另起一路——顺序靠的就是只有一路。 */
  async function flush() {
    if (running) return
    running = true
    cancelRetry()
    try {
      for (;;) {
        const item = outbox.value.find((o) => o.state === 'queued')
        if (!item) return
        if ((await sendOne(item)) === 'stop') {
          if (inFlight === null && outbox.value.includes(item)) scheduleRetry()
          return
        }
      }
    } finally {
      running = false
    }
  }

  /** 排一条，立刻交出去。返回它的 `client_id`（也是这次发送的 `request_id`）。 */
  function enqueue(message: Pick<Outgoing, 'content' | 'replyTo' | 'atts' | 'quotedContext'>): string {
    const clientId = crypto.randomUUID()
    outbox.value.push({ clientId, ...message, state: 'queued' })
    void flush()
    return clientId
  }

  /** 落库的那一块到了（socket 上的回声，或重连后读回的历史）：本地这条有真身了。 */
  function settle(block: Block): boolean {
    const clientId = (block.meta as Record<string, unknown> | null)?.client_id
    if (typeof clientId !== 'string') return false
    const i = outbox.value.findIndex((o) => o.clientId === clientId)
    if (i < 0) return false
    outbox.value.splice(i, 1)
    return true
  }

  function retry(clientId: string) {
    const item = outbox.value.find((o) => o.clientId === clientId)
    if (!item) return
    item.state = 'queued'
    item.error = undefined
    retryAttempt = 0
    void flush()
  }

  function drop(clientId: string) {
    outbox.value = outbox.value.filter((o) => o.clientId !== clientId)
  }

  onScopeDispose(pause)

  return { outbox, enqueue, flush, settle, retry, drop, pause }
}
