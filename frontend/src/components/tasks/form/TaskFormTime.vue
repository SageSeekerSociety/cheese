<script setup lang="ts">
// 「时间」那一节：报名开始与截止（都可空）、领取后几天内完成。
//
// 它只画：三样值都是 props 进来的，改一次往上报一次。今天以前的日子点不动。
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

const { t } = useI18n()

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
    <div class="tf-grid">
      <BaseField
        :label="t('tasks.form.registration')"
        :error="errorOf(registrationStartAtControl) ?? errorOf(deadlineControl)"
      >
        <div class="tf-inline tf-dates">
          <VDateInput
            v-model="registrationStartAt"
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

      <BaseField :label="t('tasks.form.completion')" required :error="errorOf(defaultDeadlineControl)">
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
