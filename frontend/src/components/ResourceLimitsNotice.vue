<script setup lang="ts">
import type { ResourceLimits } from '@/api'

import { onMounted, ref } from 'vue'

import { getResourceLimits } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

// own：项目要建在自己名下（只有自己的那个团队），名额和「工作电脑」页就不说成团队的。
defineProps<{ own?: boolean }>()

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
      <div>
        {{
          t(own ? 'work.resourceLimits.machinesOwn' : 'work.resourceLimits.machines', {
            count: limits.max_machines_per_team,
          })
        }}
      </div>
    </template>
    <template v-else-if="failed">
      {{ t('work.resourceLimits.failed') }}
      <BaseButton kind="secondary" size="sm" @click="load">{{ t('work.resourceLimits.retry') }}</BaseButton>
    </template>
    <template v-else>{{ t('work.resourceLimits.loading') }}</template>
  </div>
</template>
