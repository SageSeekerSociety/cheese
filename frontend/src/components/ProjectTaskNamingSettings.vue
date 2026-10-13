<script setup lang="ts">
// 项目设置「任务命名」：两档，选中即保存；不能管理的人只看得见。
//
// 只吃 props、只往上发事件（`frontend_grade.py` 的 A 级）：取数与发请求在
// `composables/useProjectTaskNamingSettings.ts` 里，由设置页接线。这里只画收到的状态，
// 选中了把那一档报上去。行为见后端 `room_task/naming.py`。
import type { TaskNaming, TaskNamingMode } from '@/api'

import { t } from '@/i18n'

defineProps<{
  /** 读到的两档现状；还没读到是 null。 */
  state: TaskNaming | null
  /** 上一句没说完的话：读失败或保存失败。 */
  error: string
  /** 一次保存还在路上。 */
  busy: boolean
}>()

const emit = defineEmits<{ (e: 'choose', mode: TaskNamingMode): void }>()

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
          @click="emit('choose', o.value)"
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
