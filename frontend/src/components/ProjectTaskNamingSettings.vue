<script setup lang="ts">
import type { TaskNaming, TaskNamingMode } from '../api'

import { onMounted, ref, watch } from 'vue'

import { holdRevealGate } from '@/composables/useRevealGate'

import { getTaskNaming, setTaskNaming } from '../api'

import { t } from '@/i18n'

// 任务命名：没起名的任务由平台自动起名、方向变了再改（默认），还是全由人来起名。
// 两档都不碰人定过的名字。行为见后端 room_task/naming.py。选中即保存：两个选项、
// 没有要一起提交的别的字段。
const props = defineProps<{ projectId: string }>()

const state = ref<TaskNaming | null>(null)
const error = ref('')
const busy = ref(false)

const OPTIONS: { value: TaskNamingMode; title: string; detail: string }[] = [
  {
    value: 'auto',
    title: t('work.projectSettings.taskNaming.autoTitle'),
    detail: t('work.projectSettings.taskNaming.autoDetail'),
  },
  {
    value: 'manual',
    title: t('work.projectSettings.taskNaming.manualTitle'),
    detail: t('work.projectSettings.taskNaming.manualDetail'),
  },
]

async function load() {
  error.value = ''
  try {
    state.value = await getTaskNaming(props.projectId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.projectSettings.taskNaming.loadFailed')
  }
}

async function choose(mode: TaskNamingMode | null) {
  if (!mode || !state.value?.can_manage || mode === state.value.mode) return
  busy.value = true
  error.value = ''
  try {
    state.value = await setTaskNaming(props.projectId, mode)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.projectSettings.taskNaming.saveFailed')
  } finally {
    busy.value = false
  }
}

const releaseGate = holdRevealGate()
onMounted(() => load().finally(releaseGate))
watch(() => props.projectId, load)
</script>

<template>
  <div>
    <p class="t-body c-muted mb-4">{{ t('work.projectSettings.taskNaming.intro') }}</p>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
    <template v-if="state">
      <div
        class="naming-options"
        role="radiogroup"
        :aria-label="t('work.projectSettings.taskNaming.label')"
        data-testid="task-naming-mode"
      >
        <button
          v-for="o in OPTIONS"
          :key="o.value"
          type="button"
          role="radio"
          class="naming-option"
          :class="{ 'naming-option--on': state.mode === o.value }"
          :aria-checked="state.mode === o.value"
          :disabled="busy || !state.can_manage"
          @click="choose(o.value)"
        >
          <span class="naming-option__dot" aria-hidden="true" />
          <span class="naming-option__text">
            <span class="naming-option__title">{{ o.title }}</span>
            <span class="naming-option__detail c-muted">{{ o.detail }}</span>
          </span>
        </button>
      </div>
      <p v-if="!state.available && state.mode === 'auto'" class="t-caption c-muted mt-2">
        {{ t('work.projectSettings.taskNaming.fallback') }}
      </p>
      <p v-if="!state.can_manage" class="t-caption c-muted mt-2">
        {{ t('work.projectSettings.taskNaming.readOnly') }}
      </p>
    </template>
  </div>
</template>

<style scoped>
.naming-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 620px;
}

.naming-option {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  text-align: left;
  transition: border-color 0.15s ease;
}

.naming-option:hover:not(:disabled) {
  border-color: var(--line-2);
}

.naming-option:disabled {
  cursor: default;
}

.naming-option--on {
  border-color: var(--accent);
}

.naming-option__dot {
  flex: none;
  width: 14px;
  height: 14px;
  margin-top: 4px;
  border: 2px solid var(--line-2);
  border-radius: 999px;
}

.naming-option--on .naming-option__dot {
  border: 4px solid var(--accent);
}

.naming-option__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.naming-option__title {
  font-size: 14px;
  font-weight: 500;
}

.naming-option__detail {
  font-size: 13px;
  line-height: 1.6;
}
</style>
