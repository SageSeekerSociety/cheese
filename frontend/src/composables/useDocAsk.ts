// 问 AI 队友一句话：发成一条点了它名的评论，然后等它在那条评论下面回答。
//
// 选中文字时问（useDocAgent）和顶栏上对整篇问，用的都是这一份：一次问、在等、回答到了
// （等太久没有回答也算到了头，回答以后会出现在评论里）。
import type { SelectionTarget } from '../lib/docBubble'

import { onScopeDispose, ref } from 'vue'

import { t } from '@/i18n'

export interface DocAskOptions {
  /** 把一句话连同选中的字发成点了 AI 队友名的评论；回执是那条评论的 id。 */
  ask: () => ((target: SelectionTarget, question: string) => Promise<string>) | undefined
  /** 那条评论下 AI 队友的回答；还没有时是 null。 */
  answerOf: (threadId: string) => Promise<string | null>
  onError: (message: string) => void
}

/** 隔多久看一次有没有回答，最多看多久。回答再晚也在评论里。 */
const POLL_MS = 3000
const WAIT_MS = 5 * 60_000

export function useDocAsk(options: DocAskOptions) {
  const phase = ref<'idle' | 'waiting' | 'answered'>('idle')
  const threadId = ref<string | null>(null)
  /** 回答；等太久没有回答时是 null。 */
  const answer = ref<string | null>(null)
  // 每问一次、收一次就换一个号：晚到的回答对不上号就不认。
  let attempt = 0
  let timer: ReturnType<typeof setTimeout> | undefined

  function close() {
    attempt++
    if (timer) clearTimeout(timer)
    timer = undefined
    phase.value = 'idle'
    threadId.value = null
    answer.value = null
  }

  /** 问；问题没发出去时说出来，回到没问的样子，返回 false。 */
  async function ask(target: SelectionTarget, question: string): Promise<boolean> {
    const send = options.ask()
    const body = question.trim()
    if (!send || !body) return false
    close()
    const id = attempt
    phase.value = 'waiting'
    try {
      const thread = await send(target, body)
      if (id !== attempt) return true
      threadId.value = thread
      wait(thread, id, Date.now())
      return true
    } catch (error) {
      if (id !== attempt) return true
      close()
      options.onError(error instanceof Error && error.message ? error.message : t('work.room.comments.postFailed'))
      return false
    }
  }

  function wait(thread: string, id: number, started: number) {
    timer = setTimeout(async () => {
      timer = undefined
      const text = await options.answerOf(thread).catch(() => null)
      if (id !== attempt) return
      if (text) answer.value = text
      if (text || Date.now() - started >= WAIT_MS) phase.value = 'answered'
      else wait(thread, id, started)
    }, POLL_MS)
  }

  onScopeDispose(close)

  return { phase, threadId, answer, ask, close }
}

export type DocAskController = ReturnType<typeof useDocAsk>
