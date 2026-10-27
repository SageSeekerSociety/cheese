<script setup lang="ts">
// 「时间」那一节：怎么开始（直接开始 / 申请制）、两个日期（都可空）、
// 申请制的题通过后几天内完成。
//
// 它只画：几样值都是 props 进来的，改一次往上报一次。今天以前的日子点不动。
// 直接开始的题设了截止日期所有人按它交，不设就没有截止；没有「限时几天」这一格。
// 有人申请或开始做了，开始方式就定了：这一格灰着，写明为什么。
import type { TaskStartMode } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { VDateInput } from 'vuetify/labs/VDateInput'

import TaskFormSection from './TaskFormSection.vue'

import BaseField from '@/components/base/BaseField.vue'

/** `defineField` 那一对的另一半：`error-messages` / `error`。 */
type FieldControl = Record<string, any>

defineProps<{
  registrationStartAtControl: FieldControl
  deadlineControl: FieldControl
  defaultDeadlineControl: FieldControl
  /** 已经有人申请或开始做：开始方式不能再改。 */
  startModeLocked?: boolean
}>()

const registrationStartAt = defineModel<Date | null | undefined>('registrationStartAt', { required: true })
const deadline = defineModel<Date | null | undefined>('deadline', { required: true })
const defaultDeadline = defineModel<number | undefined>('defaultDeadline', { required: true })
const startMode = defineModel<TaskStartMode | undefined>('startMode', { required: true })

const { t } = useI18n()

const startOptions = computed(() => [
  { value: 'DIRECT', label: t('tasks.form.start.direct'), hint: t('tasks.form.start.directHint') },
  { value: 'APPLY', label: t('tasks.form.start.apply'), hint: t('tasks.form.start.applyHint') },
])

const errorOf = (control: FieldControl): string | undefined => control['error-messages']?.[0]

const isAllowedDates = (date: unknown) => {
  if (!(date instanceof Date)) return false
  const now = new Date()
  now.setHours(0, 0, 0, 0)
  date.setHours(0, 0, 0, 0)
  return date >= now
}
</script>

<template>
  <TaskFormSection :title="t('tasks.form.time')">
    <BaseField data-agent-field="start_mode" :label="t('tasks.form.start.label')">
      <v-radio-group v-model="startMode" :disabled="startModeLocked" hide-details density="compact" class="tf-choices">
        <v-radio v-for="option in startOptions" :key="option.value" :value="option.value">
          <template #label>
            <span>
              {{ option.label }}
              <span class="tf-choice-hint">{{ option.hint }}</span>
            </span>
          </template>
        </v-radio>
      </v-radio-group>
      <p v-if="startModeLocked" class="tf-locked" data-testid="start-mode-locked">
        {{ t('tasks.form.start.locked') }}
      </p>
    </BaseField>

    <!-- A task started directly: both dates may be empty; a deadline, when set, is everyone's hand-in date, not the end of an application window. -->
    <div v-if="startMode === 'DIRECT'" class="tf-grid">
      <BaseField :label="t('tasks.form.opensOn')" :error="errorOf(registrationStartAtControl)">
        <VDateInput
          v-model="registrationStartAt"
          data-agent-field="registration_start"
          :placeholder="t('tasks.form.opensNow')"
          :aria-label="t('tasks.form.opensOn')"
          :allowed-dates="isAllowedDates"
          :error="Boolean(errorOf(registrationStartAtControl))"
          prepend-icon=""
          variant="outlined"
          density="comfortable"
          clearable
          hide-details
        />
      </BaseField>
      <BaseField data-agent-field="deadline" :label="t('tasks.form.dueOn')" :error="errorOf(deadlineControl)">
        <template #default="{ id }">
          <VDateInput
            :id="id"
            v-model="deadline"
            :placeholder="t('tasks.form.dueNone')"
            :aria-label="t('tasks.form.dueOn')"
            :allowed-dates="isAllowedDates"
            :error="Boolean(errorOf(deadlineControl))"
            prepend-icon=""
            variant="outlined"
            density="comfortable"
            clearable
            hide-details
          />
        </template>
      </BaseField>
    </div>

    <div v-else class="tf-grid">
      <BaseField
        data-agent-field="deadline"
        :label="t('tasks.form.registration')"
        :error="errorOf(registrationStartAtControl) ?? errorOf(deadlineControl)"
      >
        <div class="tf-inline tf-dates">
          <VDateInput
            v-model="registrationStartAt"
            data-agent-field="registration_start"
            :placeholder="t('tasks.form.applyStart')"
            :aria-label="t('tasks.form.applyStart')"
            :allowed-dates="isAllowedDates"
            :error="Boolean(errorOf(registrationStartAtControl))"
            prepend-icon=""
            variant="outlined"
            density="comfortable"
            clearable
            hide-details
          />
          <span aria-hidden="true">–</span>
          <VDateInput
            v-model="deadline"
            :placeholder="t('tasks.form.applyEnd')"
            :aria-label="t('tasks.form.applyEnd')"
            :allowed-dates="isAllowedDates"
            :error="Boolean(errorOf(deadlineControl))"
            prepend-icon=""
            variant="outlined"
            density="comfortable"
            clearable
            hide-details
          />
        </div>
      </BaseField>

      <BaseField
        data-agent-field="days_to_submit"
        :label="t('tasks.form.completion')"
        required
        :error="errorOf(defaultDeadlineControl)"
      >
        <template #default="{ id, describedby, invalid, required }">
          <div class="tf-inline">
            <span>{{ t('tasks.form.completionPrefix') }}</span>
            <v-text-field
              :id="id"
              :model-value="defaultDeadline"
              :aria-describedby="describedby"
              :aria-invalid="invalid"
              :aria-required="required"
              :error="invalid"
              type="number"
              min="1"
              class="tf-num"
              variant="outlined"
              density="comfortable"
              hide-details
              @update:model-value="defaultDeadline = $event === '' ? undefined : Number($event)"
            />
            <span>{{ t('tasks.form.completionSuffix') }}</span>
          </div>
        </template>
      </BaseField>
    </div>
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
<style scoped>
.tf-dates {
  flex-wrap: nowrap;
}

.tf-dates > :not(span) {
  flex: 1 1 0;
  min-width: 0;
}

.tf-locked {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
