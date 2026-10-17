// 打开的那一份 Office 文件和上一版比：第一次交付比任务开始时的版本，被退回过的比
// 退回时那一版（`backend/app/domain/review/document_compare.py`）。
import type { Ref } from 'vue'
import type { OfficeComparison } from '../../types/reviewComment'

import { computed, ref, watch } from 'vue'

import { reviewComparison } from '../../api/reviewComments'
import { placeKind } from '../../lib/reviewPlace'

import { t } from '@/i18n'

export interface OfficeCompareDeps {
  /** 这一格看的那件任务。 */
  task: Ref<string | null>
  /** 打开的那一份文件。 */
  path: Ref<string | null>
}

/** 一份 Office 文件「和上一版比」的那一小撮状态与动作。 */
export function usePanelOfficeCompare(deps: OfficeCompareDeps) {
  const { task: selectedTask, path: openPath } = deps

  const canCompare = computed(() => !!selectedTask.value && !!openPath.value && placeKind(openPath.value) !== 'line')
  const comparing = ref(false)
  const comparison = ref<OfficeComparison | null>(null)
  const comparisonLoading = ref(false)
  const comparisonError = ref('')
  let comparisonSeq = 0
  watch(openPath, () => {
    comparing.value = false
    comparison.value = null
  })
  async function toggleCompare() {
    comparing.value = !comparing.value
    const task = selectedTask.value
    const path = openPath.value
    if (!comparing.value || !task || !path) return
    const mine = ++comparisonSeq
    comparisonLoading.value = true
    comparisonError.value = ''
    try {
      const res = await reviewComparison(task, path)
      if (mine === comparisonSeq) comparison.value = res.comparison
    } catch (e) {
      if (mine === comparisonSeq)
        comparisonError.value = e instanceof Error ? e.message : t('work.room.review.compareUnavailable')
    } finally {
      if (mine === comparisonSeq) comparisonLoading.value = false
    }
  }

  return { canCompare, comparing, comparison, comparisonLoading, comparisonError, toggleCompare }
}
