<script setup lang="ts">
/**
 * 「这台设备」（`/users/settings/this-device`，只在桌面 app 里有）。取数、保存、接入和断开
 * 都在这里，画法在同目录的 `ThisDeviceView.vue`——这一页只做它的容器。
 */
import { computed, onMounted, watch } from 'vue'

import { useThisDevice } from '@/composables/useThisDevice'

import { deviceFlow, startConnecting } from '@/lib/desktop'
import ThisDeviceView from '@/views/user/settings/ThisDeviceView.vue'

const here = useThisDevice()
onMounted(here.load)

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
    :loading="here.loading.value && !here.device.value"
    :connected="!!here.device.value"
    :online="!!here.device.value?.online"
    :connecting="connecting"
    :name="here.device.value?.name ?? ''"
    :teams="here.teams.value"
    :team-ids="here.device.value?.team_ids ?? []"
    :claude-logged-in="!!here.claudeCode.value?.logged_in"
    :claude-plan="here.claudeCode.value?.subscription_type ?? null"
    :claude-state="here.claudeLogin.value"
    :error="here.error.value"
    @connect="startConnecting"
    @rename="here.rename"
    @teams="here.setTeams"
    @claude-login="here.logInClaudeCode"
    @claude-cancel="here.cancelClaudeCodeLogin"
    @claude-logout="here.logOutClaudeCode"
    @disconnect="here.disconnect"
  />
</template>
