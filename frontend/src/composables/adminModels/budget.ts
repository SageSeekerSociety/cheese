// 额度那一段的写：给一个项目的网关 key 设「刹车值」（`max_budget_usd`）。
//
// 它只重拉自己那一段（`projects`），不动模型表 —— 改的是别的项目的刹车值，
// 和模型那一段没有关系。
import type { Ref } from 'vue'
import type { ProjectRow } from '@/lib/adminModels'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { message } from './shared'

import { setGatewayProjectBudget } from '@/api'

export interface AdminModelsBudgetHost {
  /** 重新读项目那一小段（`adminModels/reads.ts` 的 `loadProjects`）。 */
  loadProjects: () => Promise<void>
  notice: Ref<string | null>
}

export function useAdminModelsBudget(host: AdminModelsBudgetHost) {
  const { t } = useI18n()
  const { loadProjects, notice } = host

  const budgetOpen = ref(false)
  const budgetProject = ref<ProjectRow | null>(null)
  const budgetSaving = ref(false)
  const budgetError = ref<string | null>(null)

  function openBudget(row: ProjectRow) {
    budgetError.value = null
    budgetProject.value = row
    budgetOpen.value = true
  }

  async function onBudgetSubmit(maxBudgetUsd: number | null) {
    const row = budgetProject.value
    if (!row) return
    budgetSaving.value = true
    budgetError.value = null
    try {
      await setGatewayProjectBudget(row.project_id, maxBudgetUsd)
      notice.value = t('models.notice.budgetSet', { name: row.name })
      budgetOpen.value = false
      await loadProjects()
    } catch (e) {
      // 框里的错误留在框里（同表单）：改的是别的项目的刹车值，重开一次要重新找那一行。
      budgetError.value = message(e, t('models.page.budgetFailed'))
    } finally {
      budgetSaving.value = false
    }
  }

  return {
    budgetOpen,
    budgetProject,
    budgetSaving,
    budgetError,
    openBudget,
    onBudgetSubmit,
  }
}
