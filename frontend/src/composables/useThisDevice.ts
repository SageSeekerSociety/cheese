import type { MyDevice, MyTeam } from '@/cx_types'

import { ref } from 'vue'

import { listAccessibleDevices, listMyTeams } from '@/api'
import { t } from '@/i18n'
import { desktopBridge } from '@/lib/desktop'

export function useThisDevice() {
  const bridge = desktopBridge()
  const enrollments = ref<MyDevice[]>([])
  const teams = ref<MyTeam[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load() {
    if (!bridge) return
    loading.value = true
    error.value = null
    try {
      const [ids, result, mine] = await Promise.all([bridge.thisDevices(), listAccessibleDevices(), listMyTeams()])
      enrollments.value = result.devices.filter((d) => ids.includes(d.device_id))
      teams.value = mine
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : t('account.thisDevice.loadFailed')
    } finally {
      loading.value = false
    }
  }
  return { available: bridge !== null, enrollments, teams, loading, error, load }
}
