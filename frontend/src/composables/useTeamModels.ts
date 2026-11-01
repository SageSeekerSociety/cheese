import type { AccountConnectionInput } from '@/types/modelConnections'
import type { AccountConnection, ModelConnection } from '@/types/modelConnections'

import { computed, inject, onMounted, ref, watch } from 'vue'

import {
  addAccountConnection,
  checkAccountConnection,
  checkModelConnection,
  listModelConnections,
  type PersonalConnectionChoice,
  personalConnectionChoices,
  putPersonalResource,
  removeAccountConnection,
  removePersonalResource,
  renameAccountConnection,
  renameModelConnection,
  revokeModelConnection,
  setConnectionModels,
  startTeamClaudeLogin,
} from '@/api/modelConnections'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { desktopBridge } from '@/lib/desktop'
import { toast } from '@/lib/toast'

export function useTeamModels() {
  const team = inject(teamDataInjectionKey, ref())
  const teamId = computed(() => team.value?.id ?? 0)
  const connections = ref<ModelConnection[]>([])
  const held = ref<AccountConnection[]>([])
  const personalChoices = ref<PersonalConnectionChoice[]>([])
  const platformModels = ref<{ id: string; label: string }[]>([])
  const canManage = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const busy = ref('')
  const saved = ref(0)
  const loginCommand = ref<string | null>(null)
  const bridge = desktopBridge()
  let generation = 0

  async function load() {
    const id = teamId.value
    const read = ++generation
    if (!id) return
    loading.value = true
    error.value = null
    try {
      const [models, personal] = await Promise.all([listModelConnections(id), personalConnectionChoices(id)])
      if (read !== generation) return
      connections.value = models.connections
      held.value = models.account_connections
      platformModels.value = models.platform_models
      canManage.value = models.can_manage
      personalChoices.value = personal.connections
    } catch (cause) {
      if (read === generation)
        error.value = cause instanceof Error ? cause.message : t('account.modelConnections.loadFailed')
    } finally {
      if (read === generation) loading.value = false
    }
  }

  async function act(key: string, action: () => Promise<unknown>) {
    busy.value = key
    try {
      await action()
      saved.value += 1
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : t('account.modelConnections.actionFailed'))
    } finally {
      busy.value = ''
      await load()
    }
  }

  async function login() {
    await act('login', async () => {
      const result = await startTeamClaudeLogin(teamId.value)
      loginCommand.value = null
      if (bridge?.modelCredentialLogin) {
        const profiles = await bridge.enrollmentProfiles()
        const profile = Object.values(profiles)[0] ?? 'default'
        await bridge.claudeLogin(null, () => {}, profile, result)
      } else {
        loginCommand.value = result.command
      }
    })
  }

  onMounted(load)
  watch(teamId, () => {
    connections.value = []
    held.value = []
    personalChoices.value = []
    loginCommand.value = null
    void load()
  })

  return {
    refresh: load,
    teamId,
    connections,
    held,
    personalChoices,
    addPersonal: (id: string) => act('personal', () => putPersonalResource(teamId.value, id)),
    removePersonal: (id: string) => act(`${id}:remove`, () => removePersonalResource(teamId.value, id)),
    platformModels,
    canManage,
    loading,
    error,
    busy,
    saved,
    loginCommand,
    login,
    recheck: (id: string) => act(`${id}:check`, () => checkModelConnection(teamId.value, id)),
    revoke: (id: string) => act(`${id}:revoke`, () => revokeModelConnection(teamId.value, id)),
    addHeld: (input: AccountConnectionInput) => act('add', () => addAccountConnection(teamId.value, input)),
    checkHeld: (id: string) =>
      act(`${id}:check`, async () => {
        const after = await checkAccountConnection(teamId.value, id)
        if (after.check_error) toast.error(t('account.modelConnections.checkFailed', { reason: after.check_error }))
        else toast.success(t('account.modelConnections.checked'))
      }),
    renameHeld: (id: string, name: string) =>
      act(`${id}:rename`, () => renameAccountConnection(teamId.value, id, name)),
    renameDevice: (id: string, name: string) =>
      act(`${id}:rename`, () => renameModelConnection(teamId.value, id, name)),
    removeHeld: (id: string) => act(`${id}:revoke`, () => removeAccountConnection(teamId.value, id)),
    setModels: (id: string, models: string[], onDevice: boolean) =>
      act(`${id}:models`, () => setConnectionModels(teamId.value, id, models, onDevice)),
  }
}
