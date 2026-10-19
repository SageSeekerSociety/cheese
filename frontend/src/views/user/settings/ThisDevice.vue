<script setup lang="ts">
/**
 * 「这台设备」（`/users/settings/this-device`，只在桌面 app 里有）。取数、保存、接入和断开
 * 都在这里，画法在同目录的 `ThisDeviceView.vue`——这一页只做它的容器。
 */
import { computed, onMounted, watch } from 'vue'

import { useDeviceSessions } from '@/composables/useDeviceSessions'
import { useThisDevice } from '@/composables/useThisDevice'

import { deviceFlow, startConnecting } from '@/lib/desktop'
import ThisDeviceView from '@/views/user/settings/ThisDeviceView.vue'

const here = useThisDevice()
const running = useDeviceSessions(() => (here.device.value ? [here.device.value.device_id] : []))
onMounted(async () => {
  await here.load()
  await running.load()
})

// 从这一页接入的，接好后这一页跟着换成已接入的样子。
watch(
  () => deviceFlow.stage,
  (stage) => {
    if (stage === 'done') void here.load()
  }
)

const connecting = computed(() => deviceFlow.open && deviceFlow.stage === 'progress')
</script>

<template>
  <ThisDeviceView
    :sessions="running.sessions.value"
    :prevent-sleep="running.preventSleep.value"
    :sleep-saving="running.saving.value"
    :sessions-error="running.error.value"
    :loading="here.loading.value && !here.device.value"
    :connected="!!here.device.value"
    :online="!!here.device.value?.online"
    :connecting="connecting"
    :name="here.device.value?.name ?? ''"
    :teams="here.teams.value"
    :team-ids="here.device.value?.team_ids ?? []"
    :claude-logged-in="!!here.claudeCode.value?.logged_in"
    :claude-plan="here.claudeCode.value?.subscription_type ?? null"
    :claude-service="here.claudeService.value"
    :claude-state="here.claudeLogin.value"
    :can-use-model-service="here.canUseModelService"
    :error="here.error.value"
    @prevent-sleep="running.setPreventSleep"
    @connect="startConnecting"
    @rename="here.rename"
    @teams="here.setTeams"
    @claude-login="here.logInClaudeCode"
    @claude-service="here.useModelService"
    @claude-cancel="here.cancelClaudeCodeLogin"
    @claude-logout="here.logOutClaudeCode"
    @disconnect="here.disconnect"
  />
</template>
