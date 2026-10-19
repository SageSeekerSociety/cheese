<script setup lang="ts">
import type { MyTeam } from '@/types/devices'

import { ref, watch } from 'vue'

import { listMyTeams } from '@/api'
import DeviceConnectDialog from '@/components/desktop/DeviceConnectDialog.vue'
import { t } from '@/i18n'
import { cancelConnecting, desktopBridge, deviceFlow, markAsked, startConnecting } from '@/lib/desktop'
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
function asked() {
  if (accountService.user?.id) markAsked(accountService.user.id)
}
function connect(teamId: number) {
  asked()
  void startConnecting({ teamId })
}
function close() {
  asked()
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
    @later="close"
    @finish="close"
    @cancel="cancelConnecting"
  />
</template>
