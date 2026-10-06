// 一条回答上那几个写操作：赞 / 踩 / 取消投票 / 收藏 / 采纳。
//
// 画面那一半 `components/answer/AnswerCardView.vue` 只认 props、只发事件（`@upvote`
// 这类），不带网络。这些操作原来长在 `AnswerCard.vue` 里，现在收在这里，让「只画」
// 的那一半能单独渲染，同时取数那一半（`AnswerCard.vue`）与页面容器（问题详情、
// 单条回答页）共用同一份实现 —— 抄三遍是同一段代码的三份走样。
//
// `answer` 对象是从列表里传下来的同一个引用，操作直接把结果写回它（`attitudes` /
// `is_favorite`），列表跟着变；这跟原来在 `AnswerCard.vue` 里改 prop 的效果一样。
import type { Answer, Question } from '@/types'

import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import { NewAttitudeType } from '@/constants'
import { AnswersApi } from '@/network/api/answers'
import { QuestionApi } from '@/network/api/questions'

export function useAnswerActions(options: {
  /** 这条回答挂在哪道题下面。采纳要用它；取不到（例如题还没加载出来）就不采纳。 */
  question: () => Question | null | undefined
  /** 采纳成功后让列表重来一次。单条回答页不在列表里，不需要，默认空。 */
  refresh?: () => void
}) {
  const { t } = useI18n()

  async function upvote(answer: Answer) {
    const { data } = await AnswersApi.postAttitude(answer.question_id, answer.id, NewAttitudeType.Positive)
    answer.attitudes = data.attitudes
  }

  async function downvote(answer: Answer) {
    const { data } = await AnswersApi.postAttitude(answer.question_id, answer.id, NewAttitudeType.Negative)
    answer.attitudes = data.attitudes
  }

  async function cancelVote(answer: Answer) {
    const { data } = await AnswersApi.postAttitude(answer.question_id, answer.id, NewAttitudeType.None)
    answer.attitudes = data.attitudes
  }

  async function favorite(answer: Answer) {
    if (answer.is_favorite) {
      await AnswersApi.unfavorite(answer.question_id, answer.id)
      answer.is_favorite = false
    } else {
      await AnswersApi.favorite(answer.question_id, answer.id)
      answer.is_favorite = true
    }
  }

  async function accept(answer: Answer) {
    const question = options.question()
    if (!question) return
    await QuestionApi.acceptAnswer(question.id, answer.id)
    toast.success(t('questions.answer.acceptSuccess'))
    options.refresh?.()
  }

  return { upvote, downvote, cancelVote, favorite, accept }
}
