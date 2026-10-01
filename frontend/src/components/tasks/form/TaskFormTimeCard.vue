<script setup lang="ts">
// 时间设置那张卡：报名开始（可空）、报名截止（可空 = 不设截止）、领取题目后的默认天数。
//
// 它只画：三样值都是 props 进来的，改一次往上报一次，红字由 `defineField` 的另一半给。
//
// 日期能不能选这条规矩留在这里，因为它只跟这两个日期框有关，而且是「画」的一部分：
// 今天以前的日子点不动。原来的写法如此，搬出来时一个字没动。
import { useI18n } from 'vue-i18n'
import { VDateInput } from 'vuetify/labs/VDateInput'

import TaskFormSection from './TaskFormSection.vue'

/** `defineField` 那一对的另一半：`error-messages` / `error`，`v-bind` 到控件上。 */
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

const isAllowedDates = (date: unknown) => {
  if (!(date instanceof Date)) return false
  const now = new Date()
  now.setHours(0, 0, 0, 0)
  date.setHours(0, 0, 0, 0)
  return date >= now
}
</script>

<template>
  <TaskFormSection icon="mdi-clock-outline" :title="t('tasks.form.schedule')">
    <v-row dense>
      <v-col cols="12" md="6">
        <v-date-input
          v-model="registrationStartAt"
          :label="t('tasks.form.registrationStartAt')"
          density="comfortable"
          :required="false"
          v-bind="registrationStartAtControl"
          :allowed-dates="isAllowedDates"
          :hint="t('tasks.form.registrationStartAtHint')"
        ></v-date-input>
      </v-col>
      <v-col cols="12" md="6">
        <v-date-input
          v-model="deadline"
          :label="t('tasks.form.deadline')"
          clearable
          :hint="t('tasks.form.deadlineHint')"
          density="comfortable"
          v-bind="deadlineControl"
          :allowed-dates="isAllowedDates"
        ></v-date-input>
      </v-col>
      <v-col cols="12">
        <v-text-field
          v-model.number="defaultDeadline"
          :label="t('tasks.form.defaultDeadline')"
          type="number"
          required
          :prefix="t('tasks.form.defaultDeadlinePrefix')"
          :suffix="t('tasks.form.defaultDeadlineSuffix')"
          min="1"
          v-bind="defaultDeadlineControl"
        ></v-text-field>
      </v-col>
    </v-row>
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
