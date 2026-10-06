// 一道题下面那串回答的取数：翻页、刷新、加载更多，以及「这次没读到」的那个错误。
//
// 它原来是 `components/questions/AnswerList.vue` 里的一段 `usePaging`。页面容器
// （问题详情下的回答列表）要自己取数、再把数据递给只吃 props 的视图，所以这段挪到
// 这里，`AnswerList.vue` 与容器共用同一份实现。
import { onMounted } from 'vue'

import { usePaging } from '@/utils/paging'

import { AnswersApi } from '@/network/api/answers'

export function useAnswerList(questionId: () => number) {
  const { data, refresh, loadMore, refreshing, loadingMore, error } = usePaging(async (pageStart) => {
    const {
      data: { answers, page },
    } = await AnswersApi.getAnswers(questionId(), pageStart)
    return { data: answers, page }
  })

  /** 「重试」先把上一次的错清掉再拉：`usePaging` 自己不重置 `error`，不擦的话重试
   *  成功之后画面上还挂着那行「没读到」。 */
  async function retry() {
    error.value = null
    await refresh()
  }

  onMounted(retry)

  return {
    answers: data,
    refreshing,
    loadingMore,
    loadMore,
    refresh,
    retry,
    /** 非空表示这一次没读到：列表空是假的，视图该画读失败而不是「暂时没有回答」。 */
    error,
  }
}
