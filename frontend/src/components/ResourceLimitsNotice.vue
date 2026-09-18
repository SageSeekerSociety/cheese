<script setup lang="ts">
import type { ResourceLimits } from '@/api'

import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getResourceLimits } from '@/api'

const { t } = useI18n()

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
      <div>{{ t('global.resourceLimits.projectCount') }}</div>
      <div>{{ t('global.resourceLimits.runtime', { count: limits.max_concurrent_turns }) }}</div>
      <div>{{ t('global.resourceLimits.machines', { count: limits.max_machines_per_team }) }}</div>
    </template>
    <template v-else-if="failed">
      {{ t('global.resourceLimits.loadFailed') }}
      <v-btn size="small" variant="text" @click="load">{{ t('global.retry') }}</v-btn>
    </template>
    <template v-else>{{ t('global.loading') }}</template>
  </div>
</template>
