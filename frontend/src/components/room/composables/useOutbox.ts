/**
 * 发件箱：**已经打出去、但库里还没有**的那几条消息。
 *
 * 「人发的消息立即显示，绝不排在 AI turn 后面。别的都可以错，现场不能错」——
 * 而在这之前，发送是把帧塞进 socket 就完了：屏幕上什么都没有，要等后端落库
 * （名册查询、mention 解析、通知写入）再广播回来才显示。快的时候看不出，慢的
 * 时候你会以为自己的消息发丢了。
 *
 * 所以消息先进这里、立刻出现在时间线末尾，再去对账：后端把 `client_id` 原样戳回
 * 块上，回声一到就把本地这条换成真的。对不上账的那条不会消失，它变成一条能重试
 * 的行——安静地丢掉一句话，比显示一条「未送达」糟得多。
 *
 * 这里不认识话题，也不认识轮次。「发出去之后房间该有什么反应」（等回复的指示、
 * 滚到底、清掉回复目标）是房间壳的事。
 */

import type { Ref } from 'vue'
import type { Block, ChatAttachment, WsClientChatMessage } from '../../../cx_types'
import type { Outgoing } from '../../../lib/composerDrafts'

import { onScopeDispose, ref } from 'vue'

/**
 * 等回声等多久算没送到。宁可长一点：误报「未送达」比晚一点显示更伤——房间里
 * 已经有过一次这种误报（#539）。
 */
const ECHO_TIMEOUT_MS = 30_000

export function useOutbox(options: {
  /** 把一帧交给链路；没连上返回 false。 */
  post: (message: WsClientChatMessage) => boolean
  connected: Ref<boolean>
  /**
   * 一条消息等满了整个超时都没有回声。链路自己说 OPEN 也不算数——先请它换一条新
   * 的；换成了返回 true，这条消息会跟着新链路重来，而不是被判成「未送达」。
   */
  onStale: () => boolean
}) {
  const outbox = ref<Outgoing[]>([])
  const echoTimers = new Map<string, ReturnType<typeof setTimeout>>()

  function clearEchoTimer(clientId: string) {
    const t = echoTimers.get(clientId)
    if (t) clearTimeout(t)
    echoTimers.delete(clientId)
  }

  /** 把所有还在等的计时器停掉（切话题、卸载）。 */
  function cancelTimers() {
    for (const id of [...echoTimers.keys()]) clearEchoTimer(id)
  }

  function markFailed(clientId: string) {
    clearEchoTimer(clientId)
    const item = outbox.value.find((o) => o.clientId === clientId)
    if (!item || item.state !== 'sending') return
    // No durable echo for the full timeout: the link is gone whatever OPEN says.
    if (!options.onStale()) item.state = 'failed'
  }

  /** Hand every queued message to the link, if there is a link to hand it to. */
  function flush() {
    if (!options.connected.value) return
    for (const item of outbox.value) {
      if (item.state !== 'queued') continue
      const msg: WsClientChatMessage = {
        type: 'message',
        content: item.content,
        reply_to: item.replyTo,
        attachments: item.atts,
        client_id: item.clientId,
      }
      if (!options.post(msg)) return
      item.state = 'sending'
      clearEchoTimer(item.clientId)
      echoTimers.set(
        item.clientId,
        setTimeout(() => markFailed(item.clientId), ECHO_TIMEOUT_MS)
      )
    }
  }

  /** 排一条，立刻交出去。返回它的 `client_id`。 */
  function enqueue(message: { content: string; replyTo?: string; atts?: ChatAttachment[] }): string {
    const clientId = `c${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    outbox.value.push({ clientId, ...message, state: 'queued' })
    flush()
    return clientId
  }

  /** The echo came home — this local copy has a real block now. */
  function settle(block: Block): boolean {
    const clientId = (block.meta as Record<string, unknown> | null)?.client_id
    if (typeof clientId !== 'string') return false
    const i = outbox.value.findIndex((o) => o.clientId === clientId)
    if (i < 0) return false
    clearEchoTimer(clientId)
    outbox.value.splice(i, 1)
    return true
  }

  /**
   * 服务端明确拒了一条。返回它认不认得这个 `client_id`——不认得的话那句话是说给
   * 整个房间听的，该上横幅而不是钉在某一行上。
   */
  function fail(clientId: string, message: string): boolean {
    const item = outbox.value.find((o) => o.clientId === clientId)
    if (!item) return false
    clearEchoTimer(clientId)
    item.state = 'failed'
    item.error = message
    return true
  }

  /**
   * 链路没了。正在等回声的那几条失去了通道——回到队列，而不是让它们的计时器
   * 判定「没送到」。
   */
  function requeueSending() {
    for (const item of outbox.value) {
      if (item.state === 'sending') {
        clearEchoTimer(item.clientId)
        item.state = 'queued'
      }
    }
  }

  function retry(clientId: string) {
    const item = outbox.value.find((o) => o.clientId === clientId)
    if (!item) return
    item.state = 'queued'
    item.error = undefined
    flush()
  }

  function drop(clientId: string) {
    clearEchoTimer(clientId)
    outbox.value = outbox.value.filter((o) => o.clientId !== clientId)
  }

  onScopeDispose(cancelTimers)

  return { outbox, enqueue, flush, settle, fail, requeueSending, retry, drop, cancelTimers }
}
