/**
 * 整组提交：把那几道题的答案一次交上去，回来的块原地换掉。
 *
 * 形状和 `useAskResponse` 一样——组件只往上发意图，请求在这一层发，回来的一块换回
 * 时间线（组件不取数据，见 `.claude/rules/architecture.md`）。换回去之后题上的
 * `answer_log` 有人答了，卡片自己就从「答题」长成「答案」的样子。
 *
 * 一次交上去的是整组：服务端把每道题都写上，房间里只留下一条消息（正文逐题列着
 * 答案）。所以回来的不止一道题。
 */
import type { Ref } from 'vue'
import type { Block } from '../cx_types'

import { ref } from 'vue'

import { answerGroup, type GroupAnswer } from '../api/blocks'

import { t } from '@/i18n'

export interface AskGroupAnswerDeps {
  /** 时间线：改完的那几块原地换掉（`useTimeline.replace`）。 */
  timeline: { replace: (block: Block) => void }
  /** 失败时那面横幅。 */
  errorMsg: Ref<string | null>
}

export function useAskGroupAnswer(deps: AskGroupAnswerDeps) {
  /** 已经在路上：挡住连点两下交两次。 */
  const busy = ref(false)

  async function submit(questionId: string, answers: Record<string, GroupAnswer>): Promise<boolean> {
    if (busy.value) return false
    busy.value = true
    deps.errorMsg.value = null
    try {
      const result = await answerGroup(questionId, answers)
      for (const question of result.questions) deps.timeline.replace(question)
      return true
    } catch (e) {
      deps.errorMsg.value = e instanceof Error ? e.message : t('ask.group.failed')
      return false
    } finally {
      busy.value = false
    }
  }

  return { submit, busy }
}
