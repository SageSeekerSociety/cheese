// 答案上那几个动作：采纳、投票（赞同/反对/撤回）、收藏。请求、请求成功后的 toast、
// 以及「这份答案现在几分、收没收藏」都落在这里；卡片（`components/answer/AnswerCard.vue`
// 和它上面的列表画面）只画收到的状态、把要点的事交上来——画面不吃 API 层
// （.claude/rules/architecture.md）。所以每个动作都收一份 `answer`，由调用方把是哪一份
// 一并交上来；写回去的分数/收藏状态直接落在这份数据上，它就是页面上那一份。
//
// 「这个人能不能采纳」也在这里，而不是留在卡片里当一句模板表达式：判据是「登着的
// 就是提问的人」，而「现在是谁登着」本身就要读 services/account。留在卡片里等于把
// 这条接缝再开一次，下一次有人来收边界时还得再看它一眼。
import type { MaybeRefOrGetter } from 'vue'
import type { Answer, Question } from '@/types'

import { computed, inject, toValue } from 'vue'

import { AnswersApi } from '@/api/answers'
import { QuestionApi } from '@/api/questions'
import { NewAttitudeType } from '@/constants'
import { t } from '@/i18n'
import { refreshInjectionKey } from '@/keys'
import { toast } from '@/lib/toast'
import { currentUserId } from '@/services/account'

/**
 * @param question 这些答案属于哪道题。没有（还在路上、或者答案被单独挂起来）时
 *   就只能投票、收藏，不能采纳。
 */
export function useAnswerActions(question: MaybeRefOrGetter<Question | null | undefined> = null) {
  // 采纳之后让页面重取一遍。这份注入项今天没有提供方（`Detail.vue` 只 provide 了
  // questionData），所以它现在恒等于那句空函数——照旧留着，不在这条线上顺手改行为。
  const refresh = inject(refreshInjectionKey, () => {})

  /** 只有提问的人能采纳，而提问的人就是眼下登着的这个人。 */
  const canAccept = computed(() => {
    const q = toValue(question)
    return !!q && q.author.id === currentUserId.value
  })

  async function accept(answer: Answer): Promise<void> {
    const q = toValue(question)
    if (!q) return
    await QuestionApi.acceptAnswer(q.id, answer.id)
    toast.success(t('questions.answer.acceptSuccess'))
    refresh()
  }

  /** 三种投票是同一个请求的三个取值，差别只在服务端的 `attitude_type`。 */
  async function vote(answer: Answer, type: NewAttitudeType): Promise<void> {
    const { attitudes } = await AnswersApi.postAttitude(answer.question_id, answer.id, type)
    answer.attitudes = attitudes
  }

  async function favorite(answer: Answer): Promise<void> {
    if (answer.is_favorite) {
      await AnswersApi.unfavorite(answer.question_id, answer.id)
      answer.is_favorite = false
    } else {
      await AnswersApi.favorite(answer.question_id, answer.id)
      answer.is_favorite = true
    }
  }

  return {
    canAccept,
    accept,
    upvote: (answer: Answer) => vote(answer, NewAttitudeType.Positive),
    downvote: (answer: Answer) => vote(answer, NewAttitudeType.Negative),
    cancelVote: (answer: Answer) => vote(answer, NewAttitudeType.None),
    favorite,
  }
}
