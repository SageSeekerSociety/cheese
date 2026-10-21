import type { MaybeRefOrGetter } from 'vue'
import type { OwnAgentsSettings } from '@/types/ownAgents'

import { ref, toValue, watch } from 'vue'

import { addOwnAgent, getOwnAgents, removeOwnAgent } from '@/api/ownAgents'
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

  async function save(change: () => Promise<OwnAgentsSettings>) {
    saving.value = true
    error.value = ''
    try {
      state.value = await change()
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.ownAgents.saveFailed')
    } finally {
      saving.value = false
    }
  }

  /** Instantiate a team connection in the project. */
  function add(connectionId: string, model: string, deviceId: string) {
    return save(() => addOwnAgent(toValue(projectId), connectionId, model, deviceId))
  }

  /** Retire a project teammate. */
  function remove(handle: string) {
    return save(() => removeOwnAgent(toValue(projectId), handle))
  }

  watch(() => toValue(projectId), load)

  return { state, error, saving, load, add, remove }
}
