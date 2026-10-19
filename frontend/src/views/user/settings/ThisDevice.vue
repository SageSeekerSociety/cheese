<script setup lang="ts">
import { onMounted, watch } from 'vue'

import { useDeviceSessions } from '@/composables/useDeviceSessions'
import { useThisDevice } from '@/composables/useThisDevice'

import ThisDeviceView from './ThisDeviceView.vue'

import { deviceFlow } from '@/lib/desktop'
const here = useThisDevice()
const running = useDeviceSessions(() => here.enrollments.value.map((device) => device.device_id))
onMounted(async () => {
  await here.load()
  await running.load()
})
watch(
  () => deviceFlow.stage,
  (stage) => {
    if (stage === 'done') void here.load()
  }
)
</script>
<template>
  <ThisDeviceView
    :loading="here.loading.value"
    :enrollments="here.enrollments.value"
    :teams="here.teams.value"
    :error="here.error.value"
    :sessions="running.sessions.value"
    :prevent-sleep="running.preventSleep.value"
    :sleep-saving="running.saving.value"
    :sessions-error="running.error.value"
    @prevent-sleep="running.setPreventSleep"
  />
</template>
