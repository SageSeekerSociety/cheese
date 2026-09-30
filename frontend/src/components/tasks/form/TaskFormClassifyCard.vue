<script setup lang="ts">
// 分类标签那张卡：所属分类（题目落在哪个分类下）和主题标签（可以选好几个）。
//
// 两张表都是算好了传进来的（`{ title, value }` 那一对），这一件不认 `SpaceCategory`
// 也不认 `Topic` —— 它只画两张下拉，值改一次往上报一次。
//
// 分类那一条在没有可选分类的时候整条不画（原来那句 `categories.length > 0` 的口径
// 一样：`categoryItems` 就是 `categories` 逐条映射过来的）。
import { useI18n } from 'vue-i18n'

import TaskFormSection from './TaskFormSection.vue'

/** `defineField` 那一对的另一半：`error-messages` / `error`，`v-bind` 到控件上。 */
type FieldControl = Record<string, any>

defineProps<{
  /** 所属分类的选项（外面从 `categories` 映射好）。 */
  categoryItems: { title: string; value: number }[]
  /** 主题标签的选项（外面从 `classificationTopics` 映射好）。 */
  topicItems: { title: string; value: number }[]
  categoryIdControl: FieldControl
  topicsControl: FieldControl
}>()

const { t } = useI18n()

const categoryId = defineModel<number | undefined>('categoryId', { required: true })
const topics = defineModel<number[] | undefined>('topics', { required: true })
</script>

<template>
  <TaskFormSection icon="mdi-tag-multiple" title="分类标签">
    <v-row>
      <v-col cols="12" md="6">
        <v-select
          v-if="categoryItems.length > 0"
          v-model="categoryId"
          autocomplete="off"
          :items="categoryItems"
          :label="t('spaces.detail.tasks.category')"
          item-title="title"
          item-value="value"
          v-bind="categoryIdControl"
        >
          <template #prepend-inner>
            <v-icon size="small">mdi-shape</v-icon>
          </template>
        </v-select>
      </v-col>

      <v-col cols="12" md="6">
        <v-select
          v-model="topics"
          autocomplete="off"
          :items="topicItems"
          :label="t('spaces.detail.tasks.topic')"
          chips
          multiple
          v-bind="topicsControl"
        ></v-select>
      </v-col>
    </v-row>
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
