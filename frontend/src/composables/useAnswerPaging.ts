// 一页一页往下翻的答案列表。`components/questions/AnswerList.vue` 只画 `usePaging`
// 交回来的那些状态（骨架屏、空板、加载更多），取数这一句落在这里——组件不吃 API 层
// （.claude/rules/architecture.md）。
import type { MaybeRefOrGetter } from 'vue'
import type { Answer } from '@/types'

import { toValue } from 'vue'

import { usePaging } from '@/utils/paging'

import { AnswersApi } from '@/network/api/answers'

/** @param questionId 取哪道题的答案。跟着它走的 watch 由调用方自己挂。 */
export function useAnswerPaging(questionId: MaybeRefOrGetter<number>) {
  return usePaging<Answer>(async (pageStart) => {
    const {
      data: { answers, page },
    } = await AnswersApi.getAnswers(toValue(questionId), pageStart)
    return { data: answers, page }
  })
}
