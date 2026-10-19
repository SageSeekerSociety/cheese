<script setup lang="ts">
import { onMounted, watch } from 'vue'

import { useThisDevice } from '@/composables/useThisDevice'

import ThisDeviceView from './ThisDeviceView.vue'

import { deviceFlow } from '@/lib/desktop'
const here = useThisDevice()
onMounted(here.load)
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
  />
</template>
