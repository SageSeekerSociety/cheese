<script setup lang="ts">
// 「分类与话题」那一节：一道题在侧栏哪个分类下（必选一个），带哪些话题（可多选）。
//
// 它只画：选项是外面映射好传进来的，值改一次往上报一次。
import { useI18n } from 'vue-i18n'

import TaskFormSection from './TaskFormSection.vue'

import BaseField from '@/components/base/BaseField.vue'

/** `defineField` 那一对的另一半：`error-messages` / `error`。 */
type FieldControl = Record<string, any>

defineProps<{
  categoryItems: { title: string; value: number }[]
  topicItems: { title: string; value: number }[]
  categoryIdControl: FieldControl
}>()

const categoryId = defineModel<number | undefined>('categoryId', { required: true })
const topics = defineModel<number[] | undefined>('topics', { required: true })

const { t } = useI18n()
</script>

<template>
  <!-- 分类那一格带自己的 aria-label：v-select 拿「打开 / 关闭」那句给输入框作名字，
       盖过 BaseField 的 <label for>，读屏念出来的就是「打开」。 -->
  <TaskFormSection :title="t('tasks.form.classify')">
    <div class="tf-grid">
      <BaseField :label="t('tasks.form.category')" required :error="categoryIdControl['error-messages']?.[0]">
        <template #default="{ id, describedby, invalid, required }">
          <v-select
            :id="id"
            v-model="categoryId"
            autocomplete="off"
            :aria-describedby="describedby"
            :aria-invalid="invalid"
            :aria-required="required"
            :error="invalid"
            :items="categoryItems"
            :aria-label="t('tasks.form.category')"
            :no-data-text="t('tasks.form.noCategories')"
            item-title="title"
            item-value="value"
            variant="outlined"
            density="comfortable"
            hide-details
          />
        </template>
      </BaseField>

      <BaseField :label="t('tasks.form.topics')">
        <template #default="{ id }">
          <v-autocomplete
            :id="id"
            v-model="topics"
            autocomplete="off"
            :items="topicItems"
            :placeholder="t('tasks.form.topicsPlaceholder')"
            :no-data-text="t('tasks.form.noTopics')"
            item-title="title"
            item-value="value"
            variant="outlined"
            density="comfortable"
            multiple
            chips
            closable-chips
            hide-details
          />
        </template>
      </BaseField>
    </div>
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
