<script setup lang="ts">
// 接入这台设备的对话框背后的那一半：接入流程（lib/desktop.ts 的 deviceFlow）和接好之后这台设备的
// 名称、团队、Claude Code（useThisDevice）。挂在 App.vue 上，登录时问的和设置页点的是同一个。
import { watch } from 'vue'

import { useThisDevice } from '@/composables/useThisDevice'

import DeviceConnectDialog from '@/components/desktop/DeviceConnectDialog.vue'
import { cancelConnecting, deviceFlow, markAsked, startConnecting } from '@/lib/desktop'
import accountService from '@/services/account'

defineOptions({ name: 'DeviceConnect' })

const here = useThisDevice()
const mac = /Mac/.test(navigator.userAgent)

watch(
  () => deviceFlow.stage,
  (stage) => {
    if (stage === 'done') void here.load()
  }
)

function asked() {
  const userId = accountService.user?.id
  if (typeof userId === 'number') markAsked(userId)
}

function connect() {
  asked()
  void startConnecting()
}

function close() {
  asked()
  deviceFlow.open = false
}
</script>

<template>
  <DeviceConnectDialog
    v-if="here.available"
    :open="deviceFlow.open"
    :stage="deviceFlow.stage"
    :steps="deviceFlow.steps"
    :current="deviceFlow.current"
    :percent="deviceFlow.percent"
    :failure="deviceFlow.failure"
    :mac="mac"
    :device-name="here.device.value?.name ?? ''"
    :teams="here.teams.value"
    :team-ids="here.device.value?.team_ids ?? []"
    :claude-logged-in="!!here.claudeCode.value?.logged_in"
    :claude-plan="here.claudeCode.value?.subscription_type ?? null"
    :claude-service="here.claudeService.value"
    :claude-state="here.claudeLogin.value"
    :error="here.error.value"
    @connect="connect"
    @retry="connect"
    @later="close"
    @finish="close"
    @cancel="cancelConnecting"
    @rename="here.rename"
    @teams="here.setTeams"
    @claude-login="here.logInClaudeCode"
    @claude-cancel="here.cancelClaudeCodeLogin"
  />
</template>
