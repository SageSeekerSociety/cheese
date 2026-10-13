// 项目设置里「成员自己的 Claude Code」那一块的数据：项目允不允许成员接入，以及谁接了、
// 各用哪类连接（#2991）。开关和允许的连接类型只有管理者能改；谁可以叫某一位，只有它的主人能改。
import type { MaybeRefOrGetter } from 'vue'
import type { OwnAgentCallers, OwnAgentKind, OwnAgentsSettings } from '@/types/ownAgents'

import { ref, toValue, watch } from 'vue'

import { getOwnAgents, setOwnAgentCallers, setOwnAgentsRules } from '@/api/ownAgents'
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
      state.value = await setOwnAgentsRules(toValue(projectId), { allowed })
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.ownAgents.saveFailed')
    } finally {
      saving.value = false
    }
  }

  async function setKinds(kinds: OwnAgentKind[]) {
    if (!state.value?.can_manage || !kinds.length) return
    saving.value = true
    error.value = ''
    try {
      state.value = await setOwnAgentsRules(toValue(projectId), { kinds })
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.ownAgents.saveFailed')
    } finally {
      saving.value = false
    }
  }

  async function setCallers(handle: string, callers: OwnAgentCallers) {
    saving.value = true
    error.value = ''
    try {
      state.value = await setOwnAgentCallers(toValue(projectId), handle, callers)
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.ownAgents.saveFailed')
    } finally {
      saving.value = false
    }
  }

  watch(() => toValue(projectId), load)

  return { state, error, saving, load, setAllowed, setKinds, setCallers }
}
