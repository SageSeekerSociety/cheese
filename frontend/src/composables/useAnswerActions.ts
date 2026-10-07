// 答案卡片上那几个动作：采纳、投票（赞同/反对/撤回）、收藏。请求、请求成功后的
// toast、以及「这份答案现在几分、收没收藏」都落在这里；`components/answer/AnswerCard.vue`
// 只画收到的状态、点这里给的回调——组件不吃 API 层
// （.claude/rules/architecture.md）。
//
// 「这个人能不能采纳」也在这里，而不是留在卡片里当一句模板表达式：判据是「登着的
// 就是提问的人」，而「现在是谁登着」本身就要读 services/account。留在卡片里等于把
// 这条接缝再开一次，下一次有人来收边界时还得再看它一眼。
import type { MaybeRefOrGetter, Ref } from 'vue'
import type { Answer, Question } from '@/types'

import { computed, inject, toValue } from 'vue'
import { toast } from 'vuetify-sonner'

import { NewAttitudeType } from '@/constants'
import { t } from '@/i18n'
import { refreshInjectionKey } from '@/keys'
import { AnswersApi } from '@/network/api/answers'
import { QuestionApi } from '@/network/api/questions'
import { currentUserId } from '@/services/account'

/**
 * @param answer 卡片正在画的那份答案。投票和收藏会把服务端回来的 `attitudes` /
 *   收藏状态写回它——它本来就是这份数据在页面上的那一份，调用方给的是响应式的 ref。
 * @param question 这份答案属于哪道题。没有（还在路上、或者卡片被单独挂起来）时
 *   就只能投票、收藏，不能采纳。
 */
export function useAnswerActions(answer: Ref<Answer>, question: MaybeRefOrGetter<Question | null | undefined> = null) {
  // 采纳之后让页面重取一遍。这份注入项今天没有提供方（`Detail.vue` 只 provide 了
  // questionData），所以它现在恒等于那句空函数——照旧留着，不在这条线上顺手改行为。
  const refresh = inject(refreshInjectionKey, () => {})

  /** 只有提问的人能采纳，而提问的人就是眼下登着的这个人。 */
  const canAccept = computed(() => {
    const q = toValue(question)
    return !!q && q.author.id === currentUserId.value
  })

  async function accept(): Promise<void> {
    const q = toValue(question)
    if (!q) return
    await QuestionApi.acceptAnswer(q.id, answer.value.id)
    toast.success(t('questions.answer.acceptSuccess'))
    refresh()
  }

  /** 三种投票是同一个请求的三个取值，差别只在服务端的 `attitude_type`。 */
  async function vote(type: NewAttitudeType): Promise<void> {
    const { data } = await AnswersApi.postAttitude(answer.value.question_id, answer.value.id, type)
    answer.value.attitudes = data.attitudes
  }

  async function favorite(): Promise<void> {
    if (answer.value.is_favorite) {
      await AnswersApi.unfavorite(answer.value.question_id, answer.value.id)
      answer.value.is_favorite = false
    } else {
      await AnswersApi.favorite(answer.value.question_id, answer.value.id)
      answer.value.is_favorite = true
    }
  }

  return {
    canAccept,
    accept,
    upvote: () => vote(NewAttitudeType.Positive),
    downvote: () => vote(NewAttitudeType.Negative),
    cancelVote: () => vote(NewAttitudeType.None),
    favorite,
  }
}
