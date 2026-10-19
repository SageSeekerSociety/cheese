import type { AccountConnectionInput } from '@/types/modelConnections'
import type { AccountConnection, ModelConnection, ModelServiceDevice } from '@/types/modelConnections'

import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { listTeamDevices } from '@/api'
import {
  addAccountConnection,
  checkAccountConnection,
  checkModelConnection,
  listModelConnections,
  removeAccountConnection,
  renameAccountConnection,
  renameModelConnection,
  revokeModelConnection,
  setConnectionModels,
  startTeamClaudeLogin,
} from '@/api/modelConnections'
import { checkClaudeCode } from '@/api/ownAgents'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { desktopBridge } from '@/lib/desktop'

export function useTeamModels() {
  const team = inject(teamDataInjectionKey, ref())
  const teamId = computed(() => team.value?.id ?? 0)
  const route = useRoute()
  const router = useRouter()
  const connections = ref<ModelConnection[]>([])
  const held = ref<AccountConnection[]>([])
  const devices = ref<ModelServiceDevice[]>([])
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
      const [models, enrolled] = await Promise.all([listModelConnections(id), listTeamDevices(id)])
      if (read !== generation) return
      connections.value = models.connections
      held.value = models.account_connections
      platformModels.value = models.platform_models
      canManage.value = models.can_manage
      devices.value = enrolled.devices.filter((d) => d.online).map((d) => ({ device_id: d.device_id, name: d.name }))
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

  async function login(deviceId: string) {
    const id = teamId.value
    const handle = team.value?.handle ?? ''
    await act('login', async () => {
      const [permission, enrolled] = await Promise.all([listModelConnections(id), listTeamDevices(id)])
      if (!permission.can_manage) throw new Error(t('account.modelConnections.readOnly'))
      if (!enrolled.devices.some((d) => d.device_id === deviceId))
        throw new Error(t('account.modelConnections.noDevice'))
      loginCommand.value = null
      const profiles = bridge ? await bridge.enrollmentProfiles() : {}
      const profile = profiles[deviceId]
      if (bridge && profile !== undefined) {
        await bridge.claudeLogin(false, null, () => {}, profile)
        await checkClaudeCode(deviceId)
      } else {
        const result = await startTeamClaudeLogin(id, deviceId, handle)
        loginCommand.value = result.command
        toast.info(t(result.opened ? 'account.modelConnections.loginOpened' : 'account.modelConnections.loginTerminal'))
      }
    })
  }

  async function requestedLogin() {
    const deviceId = route.query.loginDevice
    if (!bridge || typeof deviceId !== 'string' || !teamId.value) return
    await router.replace({ query: { ...route.query, loginDevice: undefined } })
    await login(deviceId)
  }

  onMounted(async () => {
    await load()
    await requestedLogin()
  })
  watch(teamId, () => {
    connections.value = []
    held.value = []
    loginCommand.value = null
    void load()
    void requestedLogin()
  })
  watch(
    () => route.query.loginDevice,
    () => void requestedLogin()
  )

  return {
    refresh: async () => {
      if (canManage.value)
        await act('refresh', () => Promise.all(devices.value.map((d) => checkClaudeCode(d.device_id))))
      else await load()
    },
    teamId,
    connections,
    held,
    devices,
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
