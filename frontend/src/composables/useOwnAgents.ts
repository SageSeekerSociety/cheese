// 项目设置里「成员自己的 Claude Code」那一块的数据：项目允不允许成员接入，以及谁接了、
// 跑在谁的哪几台电脑上（#2991）。改开关只有管理者能做。
import type { MaybeRefOrGetter } from 'vue'
import type { OwnAgentsSettings } from '@/types/ownAgents'

import { ref, toValue, watch } from 'vue'

import { getOwnAgents, setOwnAgentsAllowed } from '@/api/ownAgents'
import { t } from '@/i18n'

export function useOwnAgents(projectId: MaybeRefOrGetter<string>) {
  const state = ref<OwnAgentsSettings | null>(null)
  const error = ref('')
  const saving = ref(false)

  async function load() {
    error.value = ''
    try {
      state.value = await getOwnAgents(toValue(projectId))
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.ownAgents.loadFailed')
    }
  }

  async function setAllowed(allowed: boolean) {
    if (!state.value?.can_manage || allowed === state.value.allowed) return
    saving.value = true
    error.value = ''
    try {
      state.value = await setOwnAgentsAllowed(toValue(projectId), allowed)
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.ownAgents.saveFailed')
    } finally {
      saving.value = false
    }
  }

  watch(() => toValue(projectId), load)

  return { state, error, saving, load, setAllowed }
}
