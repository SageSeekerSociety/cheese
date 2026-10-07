// 这台设备：桌面 app 所在的这台电脑，作为一台设备在平台上的样子（名称、提供给哪些团队、
// 机主自己的 Claude Code 登录了没有），和能对它做的事。接入完成那一步和「设置 → 这台设备」
// 共用这一份。
import type { MyDevice, MyTeam } from '@/cx_types'
import type { ClaudeCodeLogin } from '@/types/ownAgents'

import { computed, ref } from 'vue'

import {
  listMyDevices,
  listMyTeams,
  registerDeviceForTeam,
  renameMyDevice,
  unbindMyDevice,
  unregisterDeviceFromTeam,
} from '@/api'
import { checkClaudeCode } from '@/api/ownAgents'
import { t } from '@/i18n'
import { desktopBridge, markAsked } from '@/lib/desktop'
import accountService from '@/services/account'
import { claudeLoginOf } from '@/types/ownAgents'

export type ClaudeLoginState = 'idle' | 'preparing' | 'browser'

export function useThisDevice() {
  const bridge = desktopBridge()
  const device = ref<MyDevice | null>(null)
  const teams = ref<MyTeam[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const claudeLogin = ref<ClaudeLoginState>('idle')

  const claudeCode = computed<ClaudeCodeLogin | null>(() => (device.value ? claudeLoginOf(device.value) : null))

  function fail(e: unknown, fallback: string) {
    error.value = e instanceof Error ? e.message : t(fallback)
  }

  async function load() {
    if (!bridge) return
    loading.value = true
    error.value = null
    try {
      const id = await bridge.thisDevice()
      const [devices, mine] = await Promise.all([
        listMyDevices()
          .then((r) => r.devices)
          .catch(() => [] as MyDevice[]),
        listMyTeams().catch(() => [] as MyTeam[]),
      ])
      device.value = devices.find((d) => d.device_id === id) ?? null
      teams.value = mine
    } catch (e) {
      fail(e, 'account.thisDevice.loadFailed')
    } finally {
      loading.value = false
    }
  }

  async function rename(name: string) {
    const d = device.value
    const clean = name.trim()
    if (!d || !clean || clean === d.name) return
    try {
      device.value = { ...d, ...(await renameMyDevice(d.device_id, clean)) }
    } catch (e) {
      fail(e, 'account.devices.renameFailed')
    }
  }

  async function setTeams(ids: number[]) {
    const d = device.value
    if (!d) return
    try {
      let next = d
      for (const id of ids.filter((x) => !d.team_ids.includes(x))) next = await registerDeviceForTeam(d.device_id, id)
      for (const id of d.team_ids.filter((x) => !ids.includes(x)))
        next = await unregisterDeviceFromTeam(d.device_id, id)
      device.value = { ...d, ...next }
    } catch (e) {
      fail(e, 'account.thisDevice.teamsFailed')
    }
  }

  async function refreshClaudeCode() {
    const d = device.value
    if (!d) return
    const login = await checkClaudeCode(d.device_id).catch(() => null)
    device.value = { ...d, claude_code: login } as MyDevice
  }

  async function logInClaudeCode(console: boolean) {
    if (!bridge || claudeLogin.value !== 'idle') return
    error.value = null
    claudeLogin.value = 'preparing'
    try {
      await bridge.claudeLogin(console, (step) => (claudeLogin.value = step))
      await refreshClaudeCode()
    } catch (e) {
      const why = e as { step?: string; detail?: string }
      if (why.step !== 'cancelled') error.value = t('account.thisDevice.claudeLoginFailed')
    } finally {
      claudeLogin.value = 'idle'
    }
  }

  async function cancelClaudeCodeLogin() {
    await bridge?.cancelClaudeLogin()
  }

  async function logOutClaudeCode() {
    try {
      await bridge?.claudeLogout()
      await refreshClaudeCode()
    } catch (e) {
      fail(e, 'account.thisDevice.claudeLogoutFailed')
    }
  }

  /** Unbinds this computer and stops its connector; it is not asked about again. */
  async function disconnect() {
    const d = device.value
    if (!d || !bridge) return
    try {
      await unbindMyDevice(d.device_id)
      await bridge.disconnectThisMachine().catch(() => {})
      const userId = accountService.user?.id
      if (typeof userId === 'number') markAsked(userId)
      device.value = null
    } catch (e) {
      fail(e, 'account.devices.unbindFailed')
    }
  }

  return {
    available: bridge !== null,
    device,
    teams,
    loading,
    error,
    claudeCode,
    claudeLogin,
    load,
    rename,
    setTeams,
    logInClaudeCode,
    cancelClaudeCodeLogin,
    logOutClaudeCode,
    disconnect,
  }
}
