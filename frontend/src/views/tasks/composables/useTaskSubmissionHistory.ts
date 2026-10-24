import type { PostTaskSubmissionReviewRequestData } from '@/types/taskBoard'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import { usePaging } from '@/utils/paging'

import { TaskBoardApi } from '@/api/taskBoard'

/**
 * 一版提交历史的取数与评审写入。
 *
 * 这些原来是 `TaskSubmissionHistory.vue` 自己做的：组件不许够到 API 层
 * （见 `.claude/rules/architecture.md`），所以取数和写评审搬到这里，组件只收 props、
 * 只发 `changed`。谁渲染它、判完之后谁要重读，由外面决定。
 *
 * `ids` 传 getter 而不是数字：切题/切领取者时（`props.taskId` 变了）取数要跟着换，
 * `usePaging` 的取数函数每次调用现取一次。`stage` 给了就只取这个阶段的提交：分了阶段的题
 * 每个阶段单独批改，「最新那一版」是这个阶段的。
 */
export function useTaskSubmissionHistory(
  ids: { taskId: () => number; participantId: () => number; stage?: () => string | null },
  onChanged: () => void = () => undefined
) {
  const { t } = useI18n()

  const {
    data: submissions,
    refresh,
    loadMore,
    hasMore,
    refreshing,
    loadingMore,
    total,
  } = usePaging(async (pageStart) => {
    const { submissions, page } = await TaskBoardApi.listSubmissions(ids.taskId(), ids.participantId(), {
      allVersions: true,
      sort_by: 'createdAt',
      sort_order: 'desc',
      pageStart: pageStart,
      pageSize: 10,
      queryReview: true,
      stage: ids.stage?.() ?? undefined,
    })
    return { data: submissions, page }
  })

  const latestSubmission = computed(() => submissions.value[0])

  /** 写下或改掉最新那一版的评审：判过的是改（PATCH），没判过的是写（POST）。判完/写坏了都重读一遍。 */
  async function submitReview(values: PostTaskSubmissionReviewRequestData) {
    const latest = latestSubmission.value
    if (!latest) return
    const patching = Boolean(latest.review && latest.review.reviewed)
    try {
      if (patching) {
        await TaskBoardApi.patchSubmissionReview(ids.taskId(), ids.participantId(), latest.id, values)
        toast.success(t('tasks.submissionHistory.reviewUpdated'))
      } else {
        await TaskBoardApi.postSubmissionReview(ids.taskId(), ids.participantId(), latest.id, values)
        toast.success(t('tasks.submissionHistory.reviewed'))
      }
      onChanged()
    } catch (error) {
      toast.error(t(patching ? 'tasks.submissionHistory.reviewUpdateFailed' : 'tasks.submissionHistory.reviewFailed'))
      console.error(error)
    } finally {
      refresh()
    }
  }

  /** 撤掉最新那一版的评审。 */
  async function cancelReview() {
    const latest = latestSubmission.value
    if (!latest || !latest.review) return
    try {
      await TaskBoardApi.deleteSubmissionReview(ids.taskId(), ids.participantId(), latest.id)
      toast.success(t('tasks.submissionHistory.reviewCanceled'))
      onChanged()
    } catch (error) {
      toast.error(t('tasks.submissionHistory.reviewCancelFailed'))
      console.error(error)
    } finally {
      refresh()
    }
  }

  return {
    submissions,
    latestSubmission,
    refresh,
    loadMore,
    hasMore,
    refreshing,
    loadingMore,
    total,
    submitReview,
    cancelReview,
  }
}
