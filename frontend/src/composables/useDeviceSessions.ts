import type { RunningDeviceSession } from '@/types/deviceSessions'

import { onUnmounted, ref } from 'vue'

import { listDeviceRunningSessions } from '@/api'
import { t } from '@/i18n'
import { desktopBridge } from '@/lib/desktop'

export function useDeviceSessions(deviceIds: () => string[]) {
  const sessions = ref<RunningDeviceSession[]>([])
  const preventSleep = ref(true)
  const saving = ref(false)
  const error = ref('')
  const bridge = desktopBridge()
  let timer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  async function load() {
    if (!bridge) return
    try {
      const [preference, listed] = await Promise.allSettled([
        bridge.preventSleep(),
        Promise.all(deviceIds().map(listDeviceRunningSessions)),
      ])
      if (preference.status === 'fulfilled') preventSleep.value = preference.value
      if (listed.status === 'fulfilled') {
        const combined = new Map<string, RunningDeviceSession>()
        for (const row of listed.value.flatMap((result) => result.sessions)) {
          const previous = combined.get(row.session_id)
          combined.set(row.session_id, { ...row, roles: [...new Set([...(previous?.roles ?? []), ...row.roles])] })
        }
        sessions.value = [...combined.values()]
      }
      error.value =
        preference.status === 'rejected' || listed.status === 'rejected' ? t('account.thisDevice.sessionsFailed') : ''
    } catch {
      error.value = t('account.thisDevice.sessionsFailed')
    } finally {
      if (!disposed) timer = setTimeout(load, 5000)
    }
  }
  async function setPreventSleep(enabled: boolean) {
    if (!bridge || saving.value) return
    saving.value = true
    try {
      preventSleep.value = await bridge.preventSleep(enabled)
      error.value = ''
    } catch {
      error.value = t('account.thisDevice.sleepFailed')
    } finally {
      saving.value = false
    }
  }
  onUnmounted(() => {
    disposed = true
    clearTimeout(timer)
  })
  return { sessions, preventSleep, saving, error, load, setPreventSleep }
}
