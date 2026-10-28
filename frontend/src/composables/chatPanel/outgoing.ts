// 打出去的那几条：发件箱（已经在屏幕上、库里还没有的）、发送本身、点一道题的快捷回复、
// 附件的下载。
//
// 发件箱自己见 room/composables/useOutbox（重试、退避、断线后接着送都是它的）；这里回答
// 的是「送出去之后这间房该有什么反应」——落库的那条接进窗口、滚到底、点亮等待指示。
import type { Ref } from 'vue'
import type { PostedMessage } from '../../api/messages'
import type { useTimeline } from '../../components/room/composables/useTimeline'
import type { Block, ChatAttachment, ReplyPreview, Topic } from '../../cx_types'
import type { QuotedContext } from '../../lib/quotedContext'
import type { useChatComposer } from '../useChatComposer'
import type { useChatPaging } from '../useChatPaging'

import { ApiError, attachmentRawUrl, downloadFile } from '../../api'
import { postChatMessage } from '../../api/messages'
import { SendRefused, useOutbox } from '../../components/room/composables/useOutbox'
import { outgoingMessageBody } from '../../lib/outgoingMessage'

/** 发件箱、发送、回复，和附件的下载。 */
export function usePanelOutgoing(deps: {
  /** 这一栏此刻读的那段对话（房间自己，或房间里的一个任务）。 */
  place: () => Topic | null
  /** 这间房：附件走它。 */
  topic: () => Topic | null
  /** 送出去、落库的那条接进窗口（usePanelSocket 的 pushBlock）。 */
  pushBlock: (block: Block) => void
  autoScroll: () => void
  scrollToBottom: () => void
  errorMsg: Ref<string | null>
  awaitingReply: Ref<boolean>
  /** 叫芝士的消息在这条主线上是不是在支线里回答。 */
  answersInThread: () => boolean
  /** 我发的一条落了库（opts.onPosted）。 */
  onPosted?: (block: PostedMessage) => void
  timeline: ReturnType<typeof useTimeline>
  /** 我自己刚打出去那几条（useTimelineMotion）。 */
  sentNow: () => Set<string>
  /** 已经送达的那几条（useTimelineMotion）。 */
  delivered: () => Set<string>
  composer: () => ReturnType<typeof useChatComposer>
  paging: () => ReturnType<typeof useChatPaging>
}) {
  // Send a message. `summon` (= 这条消息 @ 了芝士) asks 芝士 to reply; when false
  // the message is just posted (spec §7.1 默认不 @). The composer lives in TopicView and
  // drives this via the exposed ref, so the input bar can span chat + doc.
  // B3: reply target — the message this next send threads under (reply_to).
  function parentOf(m: Block): Block | ReplyPreview | undefined {
    if (!m.reply_to) return undefined
    // The parent is usually in the loaded timeline. When it is not (an older
    // page), the page carried a one-line preview with the reply; draw that.
    return deps.timeline.find(m.reply_to) ?? m.reply_preview
  }
  function showReplyCue(m: Block): boolean {
    // A reply cue is the message's own reply_to, for people and teammates
    // alike. An agent's message gets one only when it deliberately quotes
    // (chat_send's reply_to); ordinary answers carry no edge.
    return !!parentOf(m)
  }

  // 只给下载用：downloadFile 自己会带上 Authorization。显示图片不走这里。
  function imageUrl(m: Block): string {
    const room = deps.topic()
    return room ? attachmentRawUrl(room.id, m.content) : ''
  }
  async function downloadAttachment(m: Block) {
    // 成没成由 `downloadFile` 那一层弹一句（`lib/download`）。`errorMsg` 是房间那条
    // 出错提示行，还给别的事用，下载不再往它上面写，否则失败会说两遍。
    await downloadFile(imageUrl(m), m.content.split('/').pop() || 'file')
  }

  // 发件箱：已经打出去、库里还没有的那几条 —— 见 room/composables/useOutbox。
  // 「发出去之后房间该有什么反应」留在这里（下面的 `send`）。
  const {
    outbox,
    enqueue,
    flush: flushOutbox,
    settle: settleOutbox,
    retry: retrySend,
    drop: dropSend,
    pause: pauseOutbox,
  } = useOutbox({
    send: (item, signal) => {
      const room = deps.place()
      if (!room) return Promise.reject(new DOMException('no room', 'AbortError'))
      const body = outgoingMessageBody(item)
      return postChatMessage(room.id, body, signal).catch((error: unknown) => {
        // 4xx 是后端说了「不」（没权限、房间已归档、内容不合法）；408/429 和别的失败
        // 都是这一刻的事，发件箱会带同一个 id 再试。
        if (
          error instanceof ApiError &&
          error.status >= 400 &&
          error.status < 500 &&
          ![408, 429].includes(error.status)
        ) {
          throw new SendRefused(error.message)
        }
        throw error
      })
    },
    onDelivered: (block, dimmed) => {
      // 切走之后才回来的那一条属于上一个房间：它在那边的历史里，不画在这里。
      if (block.conversation_id !== deps.place()?.id) return
      deps.onPosted?.(block)
      if (dimmed) deps.delivered().add(block.id)
      deps.pushBlock(block)
      deps.autoScroll()
    },
  })

  function send(
    content: string,
    summon: boolean,
    attachments?: ChatAttachment[],
    quotedContext?: QuotedContext
  ): boolean {
    const trimmed = content.trim()
    const atts = attachments?.length ? attachments : undefined
    // An image-only send (no text) is a valid message (图片输入).
    if (!trimmed && !atts) return false
    deps.errorMsg.value = null
    deps.paging().backToNewest()
    deps
      .sentNow()
      .add(
        enqueue({ content: trimmed, replyTo: deps.composer().replyTarget.value?.id ?? undefined, atts, quotedContext })
      )
    deps.composer().clearReply()
    // Local acceptance starts the waiting indicator, before delivery — where the
    // answer comes. A summon in a channel's main line is answered in its 支线,
    // whose turn frames this line never hears, so lit here nothing would put it
    // out: the room would say it is working long after the answer landed.
    if (summon && !deps.answersInThread()) deps.awaitingReply.value = true
    deps.scrollToBottom()
    return true
  }

  // 点一道题的快捷回复：把那几个字作为对这道题的回复发出去 —— 和在输入框里打字是
  // 同一条消息，提问的那位队友因此开下一轮（后端认它是回这道题的）。输入框里写到
  // 一半的话不动。
  function replyToQuestion(question: Block, text: string) {
    deps.errorMsg.value = null
    deps.paging().backToNewest()
    deps.sentNow().add(enqueue({ content: text, replyTo: question.id }))
    if (!deps.answersInThread()) deps.awaitingReply.value = true
    deps.scrollToBottom()
  }

  return {
    outbox,
    flushOutbox,
    settleOutbox,
    retrySend,
    dropSend,
    pauseOutbox,
    send,
    replyToQuestion,
    parentOf,
    showReplyCue,
    downloadAttachment,
  }
}
