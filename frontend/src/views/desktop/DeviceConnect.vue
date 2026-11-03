<script setup lang="ts">
import type { DesktopExecutorStatus } from '@/lib/desktopApp'
import type { MyTeam } from '@/types/devices'

import { onBeforeUnmount, ref, watch } from 'vue'

import { listMyTeams } from '@/api'
import DeviceConnectDialog from '@/components/desktop/DeviceConnectDialog.vue'
import { t } from '@/i18n'
import { cancelConnecting, desktopBridge, deviceFlow, markDeclinedToConnect, startConnecting } from '@/lib/desktop'
import { desktopExecutorStatus, onDesktopExecutorStatus } from '@/lib/desktopApp'
import accountService from '@/services/account'
const teams = ref<MyTeam[]>([])
const error = ref<string | null>(null)
const available = !!desktopBridge()
const mac = /Mac/.test(navigator.userAgent)
// The app fetches what AI teammates run here in the background once connected;
// the finished dialog says how far it has got.
const prefetch = ref<DesktopExecutorStatus | null>(null)
const stopPrefetch = onDesktopExecutorStatus((status) => (prefetch.value = status))
onBeforeUnmount(stopPrefetch)
watch(
  () => deviceFlow.stage,
  async (stage) => {
    if (stage === 'done') prefetch.value = (await desktopExecutorStatus().catch(() => null)) ?? prefetch.value
  }
)
watch(
  () => deviceFlow.open,
  async (open) => {
    if (!open) return
    error.value = null
    try {
      teams.value = (await listMyTeams()).filter(
        (team) =>
          (team.personal || team.role === 'OWNER' || team.role === 'ADMIN') &&
          (deviceFlow.teamId === null || team.id === deviceFlow.teamId)
      )
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : t('teams.compute.loadFailed')
    }
  }
)
function connect(teamId: number) {
  void startConnecting({ teamId })
}
function later() {
  // 「暂不接入」 stands: the app does not connect this computer at the next launch.
  if (accountService.user?.id) markDeclinedToConnect(accountService.user.id)
  deviceFlow.open = false
}
function close() {
  deviceFlow.open = false
}
</script>
<template>
  <DeviceConnectDialog
    v-if="available"
    :open="deviceFlow.open"
    :stage="deviceFlow.stage"
    :steps="deviceFlow.steps"
    :current="deviceFlow.current"
    :percent="deviceFlow.percent"
    :failure="deviceFlow.failure"
    :mac="mac"
    :device-name="deviceFlow.deviceName"
    :teams="teams"
    :error="error"
    :prefetch="prefetch"
    @connect="connect"
    @retry="startConnecting({ teamId: deviceFlow.teamId! })"
    @later="later"
    @finish="close"
    @cancel="cancelConnecting"
  />
</template>
