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
// The one line that changes what the person can expect (tasks queue once the
// limit is reached) stays out; the rest is behind the disclosure so the project
// name field keeps its own space.
const expanded = ref(false)

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
  <div class="resource-limits text-body-2 text-medium-emphasis">
    <template v-if="limits">
      <div class="resource-limits__summary">
        <span role="status">{{ t('work.resourceLimits.concurrent', { count: limits.max_concurrent_turns }) }}</span>
        <button
          type="button"
          class="resource-limits__toggle"
          :aria-expanded="expanded"
          aria-controls="resource-limits-detail"
          @click="expanded = !expanded"
        >
          <span class="visually-hidden">{{ t('work.resourceLimits.detail') }}</span>
          <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
            <circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" stroke-width="1.4" />
            <path d="M8 7.3v3.3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
            <circle cx="8" cy="5.1" r="0.9" fill="currentColor" />
          </svg>
        </button>
      </div>
      <div v-if="expanded" id="resource-limits-detail" class="resource-limits__detail">
        <div>{{ t('work.resourceLimits.projects') }}</div>
        <div>
          {{
            t(own ? 'work.resourceLimits.machinesOwn' : 'work.resourceLimits.machines', {
              count: limits.max_machines_per_team,
            })
          }}
        </div>
      </div>
    </template>
    <template v-else-if="failed">
      <span role="status">{{ t('work.resourceLimits.failed') }}</span>
      <BaseButton kind="secondary" size="sm" @click="load">{{ t('work.resourceLimits.retry') }}</BaseButton>
    </template>
    <template v-else
      ><span role="status">{{ t('work.resourceLimits.loading') }}</span></template
    >
  </div>
</template>

<style scoped>
.resource-limits__summary {
  display: flex;
  align-items: center;
  gap: 4px;
}

.resource-limits__toggle {
  display: inline-flex;
  flex: none;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  cursor: pointer;
  align-items: center;
  justify-content: center;
}

.resource-limits__toggle svg {
  width: 16px;
  height: 16px;
}

.resource-limits__detail {
  margin-top: 4px;
}
</style>
