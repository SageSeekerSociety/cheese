<script setup lang="ts">
// 「时间」那一节：怎么开始（直接开始 / 申请制）、报名开始与截止（都可空）、
// 申请制的题通过后几天内完成。
//
// 它只画：几样值都是 props 进来的，改一次往上报一次。今天以前的日子点不动。
// 直接开始的题所有人按截止日期交，没有「限时几天」这一格。
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
      <v-radio-group v-model="startMode" hide-details density="compact" class="tf-choices">
        <v-radio v-for="option in startOptions" :key="option.value" :value="option.value">
          <template #label>
            <span>
              {{ option.label }}
              <span class="tf-choice-hint">{{ option.hint }}</span>
            </span>
          </template>
        </v-radio>
      </v-radio-group>
    </BaseField>

    <!-- 直接开始的题：开放的那一天可空，截止日期必填，是所有人交作业的期限，不写成报名的截止。 -->
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
      <BaseField data-agent-field="deadline" :label="t('tasks.form.dueOn')" required :error="errorOf(deadlineControl)">
        <template #default="{ id }">
          <VDateInput
            :id="id"
            v-model="deadline"
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
            :placeholder="t('tasks.form.registrationStart')"
            :aria-label="t('tasks.form.registrationStart')"
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
            :placeholder="t('tasks.form.registrationEnd')"
            :aria-label="t('tasks.form.registrationEnd')"
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
</style>
