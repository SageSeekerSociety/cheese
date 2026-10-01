import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

/**
 * 数据几格里反复出现的几组下拉：趋势粒度、排序方向、报名审核状态、完成状态。
 * 状态的名字和分布图同一套（`spaces.analytics.distribution.<代码>`），同一个状态在
 * 下拉里和图上叫同一个名字。
 */
export function useAnalyticsOptions() {
  const { t } = useI18n()

  const groupBy = computed(() =>
    (['day', 'week', 'month'] as const).map((value) => ({ title: t(`spaces.analytics.groupBy.${value}`), value }))
  )

  const sortOrder = computed(() =>
    (['desc', 'asc'] as const).map((value) => ({ title: t(`spaces.analytics.sortOrder.${value}`), value }))
  )

  // 第一项「全部」是 null：框里的名字（prefix）只在有值时才画，空着的框看不出是筛什么的。
  const all = computed(() => ({ title: t('spaces.analytics.all'), value: null }))

  const approval = computed(() => [
    all.value,
    ...(['NONE', 'APPROVED', 'DISAPPROVED'] as const).map((value) => ({
      title: t(`spaces.analytics.distribution.${value}`),
      value,
    })),
  ])

  const completion = computed(() => [
    all.value,
    ...(['NOT_SUBMITTED', 'PENDING_REVIEW', 'REJECTED_RESUBMITTABLE', 'FAILED', 'SUCCESS'] as const).map((value) => ({
      title: t(`spaces.analytics.distribution.${value}`),
      value,
    })),
  ])

  return { groupBy, sortOrder, approval, completion }
}
