<script setup lang="ts">
import type { ResourceLimits } from '@/api'

import { onMounted, ref } from 'vue'

import { getResourceLimits } from '@/api'
import { t } from '@/i18n'

const limits = ref<ResourceLimits | null>(null)
const failed = ref(false)

async function load() {
  failed.value = false
  try {
    limits.value = await getResourceLimits()
  } catch {
    failed.value = true
  }
}

onMounted(load)
</script>

<template>
  <div class="text-body-2 text-medium-emphasis my-3" role="status">
    <template v-if="limits">
      <div>{{ t('work.resourceLimits.projects') }}</div>
      <div>{{ t('work.resourceLimits.concurrent', { count: limits.max_concurrent_turns }) }}</div>
      <div>{{ t('work.resourceLimits.machines', { count: limits.max_machines_per_team }) }}</div>
    </template>
    <template v-else-if="failed">
      {{ t('work.resourceLimits.failed') }}
      <v-btn size="small" variant="text" @click="load">{{ t('work.resourceLimits.retry') }}</v-btn>
    </template>
    <template v-else>{{ t('work.resourceLimits.loading') }}</template>
  </div>
</template>
