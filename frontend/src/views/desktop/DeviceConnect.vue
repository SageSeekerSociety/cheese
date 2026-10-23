<script setup lang="ts">
import type { MyTeam } from '@/types/devices'

import { ref, watch } from 'vue'

import { listMyTeams } from '@/api'
import DeviceConnectDialog from '@/components/desktop/DeviceConnectDialog.vue'
import { t } from '@/i18n'
import { cancelConnecting, desktopBridge, deviceFlow, markDeclinedToConnect, startConnecting } from '@/lib/desktop'
import accountService from '@/services/account'
const teams = ref<MyTeam[]>([])
const error = ref<string | null>(null)
const available = !!desktopBridge()
const mac = /Mac/.test(navigator.userAgent)
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
    @connect="connect"
    @retry="startConnecting({ teamId: deviceFlow.teamId! })"
    @later="later"
    @finish="close"
    @cancel="cancelConnecting"
  />
</template>
