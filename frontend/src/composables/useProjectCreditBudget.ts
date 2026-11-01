import type { CreditBudgetUpdate, ProjectCreditBudget } from '@/types/creditBudgets'

import { ref, watch } from 'vue'

import { getProjectCreditBudget, saveProjectCreditBudget } from '@/api'

export function useProjectCreditBudget(projectId: () => string, options: { enabled?: () => boolean } = {}) {
  const enabled = options.enabled ?? (() => true)
  const budget = ref<ProjectCreditBudget | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)
  let version = 0

  async function load() {
    const current = ++version
    loading.value = true
    saving.value = false
    error.value = null
    budget.value = null
    try {
      const got = await getProjectCreditBudget(projectId())
      if (current === version) budget.value = got
    } catch (e) {
      if (current === version) error.value = e instanceof Error ? e.message : String(e)
    } finally {
      if (current === version) loading.value = false
    }
  }

  async function save(update: CreditBudgetUpdate) {
    const current = ++version
    saving.value = true
    error.value = null
    try {
      const got = await saveProjectCreditBudget(projectId(), update)
      if (current === version) budget.value = got
    } catch (e) {
      if (current === version) error.value = e instanceof Error ? e.message : String(e)
    } finally {
      if (current === version) saving.value = false
    }
  }

  watch(
    () => [projectId(), enabled()] as const,
    ([, on]) => {
      if (on) void load()
    },
    { immediate: true }
  )
  return { budget, loading, saving, error, load, save }
}
