import type { ChatAttachment, Topic } from '../cx_types'
import type { MentionMember } from './expandMentions'
import type { QuotedContext } from './quotedContext'

import { expandComposerMentions } from './composerMentions'
import { frozenQuote } from './quotedContext'

export interface PreviewQuestion {
  topicId: string
  content: string
  intent: 'ask-agent'
  quotedContext: QuotedContext
  /** 指出去的那一处自己不带图时就没有；页面上的一点带一张那一页的图，理由和图上
   *  画了东西的合成图一样：那句话离开图就指不明白。 */
  attachments?: ChatAttachment[]
}

/** True means accepted into the normal outbox, not delivered or answered. */
export type SubmitPreviewQuestion = (request: PreviewQuestion) => boolean

/** 预览里指出的一处，作为一条普通房间消息发出去。
 *
 * 图上画过东西时那一份合成图随行带上（`attachments`）：一句「把这里改成蓝色」离开
 * 那张画了圈和箭头的图就指不明白，而附件是 agent 真看得到的那条路——它被渲染成一条
 * 原生图片输入，不是只给人看的缩略图。 */
export interface PreviewLocate {
  message: string
  attachments?: ChatAttachment[]
}

export function createQuestionSubmit(options: {
  topic: () => Topic | null
  agentSeat: () => { handle: string; label: string } | null
  mentionPool: () => (MentionMember & { agent?: boolean })[]
  topicList: () => Topic[]
  send: (content: string, summon: boolean, attachments?: ChatAttachment[], quotedContext?: QuotedContext) => boolean
}): SubmitPreviewQuestion {
  return (request) => {
    const seat = options.agentSeat()
    if (request.intent !== 'ask-agent' || options.topic()?.id !== request.topicId || !seat || !request.content.trim())
      return false
    const pool = options.mentionPool()
    const topics = options.topicList().filter((topic) => topic.kind !== 'root')
    const authored = expandComposerMentions(request.content, seat, pool, topics)
    const token = `<@${seat.handle}>`
    const content = authored.startsWith(token) ? authored : `${token} ${authored}`
    return options.send(content, true, request.attachments, frozenQuote(request.quotedContext))
  }
}
