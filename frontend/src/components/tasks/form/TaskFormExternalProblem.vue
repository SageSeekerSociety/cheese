<script setup lang="ts">
// 「外站题」那一节：这道题在 YOJ 上是第几号题。
//
// 填了它，平台就能按名单里每个人的学号去 YOJ 核对过没过（结果在「领取者」页签里，
// 那一页同时管同步）。留空就是这道题只在知是上做，和以前一样。
//
// 为什么单独占一节而不是塞进「更多设置」：填题号是这道题有没有接外站的开关，接了的
// 老师每次都要填，藏进折叠里每次都得点开一次。它只有一格，没接的题目读过去也就是
// 多一行。
//
// 它只画：值和校验那一半是外面传进来的。
import { useI18n } from 'vue-i18n'

import TaskFormSection from './TaskFormSection.vue'

import BaseField from '@/components/base/BaseField.vue'

/** `defineField` 那一对的另一半：`error-messages` / `error`。 */
type FieldControl = Record<string, any>

defineProps<{
  problemNoControl: FieldControl
}>()

const problemNo = defineModel<string>('problemNo', { required: true })

const { t } = useI18n()
</script>

<template>
  <TaskFormSection :title="t('tasks.form.externalProblem.title')">
    <div class="tf-grid">
      <BaseField :label="t('tasks.form.externalProblem.label')">
        <template #default="{ id, describedby, invalid, required }">
          <v-text-field
            :id="id"
            v-model="problemNo"
            autocomplete="off"
            inputmode="numeric"
            :aria-describedby="describedby"
            :aria-invalid="invalid"
            :aria-required="required"
            :error="invalid"
            :error-messages="problemNoControl['error-messages']"
            :placeholder="t('tasks.form.externalProblem.placeholder')"
            variant="outlined"
            density="comfortable"
            maxlength="32"
            hide-details="auto"
          />
        </template>
      </BaseField>
    </div>
    <p class="tf-choice-hint">{{ t('tasks.form.externalProblem.hint') }}</p>
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
