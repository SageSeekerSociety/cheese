<template>
  <v-container>
    <v-row>
      <v-col cols="12" md="8" lg="9">
        <v-sheet rounded="lg" class="pa-4">
          <v-form @submit.prevent>
            <v-text-field
              v-model="title"
              autocomplete="off"
              :label="t('questions.ask.titleLabel')"
              variant="plain"
              class="question-title-input"
              hide-details="auto"
              :single-line="true"
              v-bind="titleProps"
            ></v-text-field>
            <rich-editor holder="editor" :config="editorConfig" @create="onCreate" />
            <topic-selector-view
              :model-value="topics"
              :items="topicItems"
              :loading="topicLoading"
              class="mt-4"
              v-bind="topicsProps"
              @update:model-value="onTopicsUpdate"
              @search="$emit('topicSearch', $event)"
              @focus="$emit('topicFocus', $event)"
            />
            <div class="d-flex align-center" style="gap: 16px">
              <BaseButton
                kind="secondary"
                :prepend-icon="hasBounty ? 'mdi-currency-usd-off' : 'mdi-currency-usd'"
                @click="hasBounty ? removeBounty() : addBounty()"
              >
                {{ hasBounty ? t('questions.ask.removeBounty') : t('questions.ask.addBounty') }}
              </BaseButton>
              <v-slide-x-reverse-transition mode="out-in">
                <v-slider
                  v-if="hasBounty"
                  v-model="bounty"
                  thumb-label="always"
                  v-bind="bountyProps"
                  min="1"
                  max="20"
                  step="1"
                  show-ticks
                  hide-details
                  class="flex-auto"
                >
                  <template #append>
                    <span style="vertical-align: baseline; min-width: 5rem; text-align: end">
                      <span>{{ t('questions.ask.bounty', { bounty }) }} </span><v-icon>mdi-cheese</v-icon>
                    </span>
                  </template>
                </v-slider>
              </v-slide-x-reverse-transition>
            </div>
          </v-form>
        </v-sheet>
      </v-col>

      <v-col cols="12" md="4" lg="3">
        <BaseButton kind="primary" block :loading="isSubmitting" class="mb-4" @click="submit">
          {{ t('questions.ask.submit') }}
        </BaseButton>
        <v-sheet rounded="lg" class="pa-4 mb-4">
          <div class="text-h5">{{ t('questions.ask.guide.title') }}</div>
          <p>{{ t('questions.ask.guide.tried') }}</p>
          <p>{{ t('questions.ask.guide.effort') }}</p>
          <p>{{ t('questions.ask.guide.premise') }}</p>
          <p>{{ t('questions.ask.guide.willing') }}</p>
        </v-sheet>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup lang="ts">
// 提问页**画的那一半**：标题 / 正文 / 话题 / 悬赏这套表单，连同它的校验。
//
// 只认 props、只发事件。发出去的只有两件不用拿回值的事（搜索与聚焦话题），
// 另外两件要等结果的（补建话题、提交）作为**函数 prop** 从容器递进来
// （跟 `resolveUser` 一个道理）：话题选择器自己不能碰 `TagsApi`，提交也不能自己
// 碰 `QuestionApi` 与路由。
import type EditorJS from '@editorjs/editorjs'
import type { TopicOption } from '@/composables/useTopicSelector'
import type { Topic } from '@/types'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { defaultEditorConfig } from '@/utils/editor'
import { vuetifyConfig } from '@/utils/form'

import BaseButton from '@/components/base/BaseButton.vue'
import RichEditor from '@/components/common/Editor/Editor.vue'
import TopicSelectorView from '@/components/common/TopicSelectorView.vue'

const props = defineProps<{
  /** 话题下拉里的候选项（容器按当前的输入搜出来的）。 */
  topicItems: TopicOption[]
  topicLoading: boolean
  /** 把刚选中的话题里那些「还没建成的」补建成真的，交回最终列表。 */
  resolveTopics: (topics: Topic[]) => Promise<Topic[]>
  /** 真正地提问：调接口、报成功、跳走。 */
  submit: (payload: { title: string; content: string; topics: number[]; bounty: number }) => Promise<void>
}>()

defineEmits<{
  topicSearch: [value: string]
  topicFocus: [value: string]
}>()

let editor: EditorJS
const { t } = useI18n()
const editorConfig = defaultEditorConfig()

const { handleSubmit, defineField, isSubmitting } = useForm({
  validationSchema: toTypedSchema(
    z.object({
      title: z
        .string()
        .trim()
        .refine((v) => v.length > 0, {
          message: t('questions.ask.errors.titleRequired'),
        })
        .refine((v) => v.endsWith('?') || v.endsWith('？'), {
          message: t('questions.ask.errors.titleQuestionMark'),
        }),
      topics: z
        .object({
          id: z.number(),
          name: z.string(),
        })
        .array()
        .refine((v) => v.length > 0, {
          message: t('questions.ask.errors.topicRequired'),
        }),
      bounty: z.number().min(0).max(20).default(0),
    })
  ),
})
const [title, titleProps] = defineField('title', vuetifyConfig)
const [topics, topicsProps] = defineField('topics', vuetifyConfig)
const [bounty, bountyProps] = defineField('bounty', vuetifyConfig)

const onCreate = (editorInstance: EditorJS) => {
  editor = editorInstance
}

const hasBounty = ref(false)

const addBounty = () => {
  hasBounty.value = true
  bounty.value = 1
}

const removeBounty = () => {
  hasBounty.value = false
  bounty.value = 0
}

async function onTopicsUpdate(newTopics: Topic[]) {
  // 先乐观写回，再拿容器补建之后的最终列表覆盖一次。
  topics.value = newTopics
  topics.value = await props.resolveTopics(newTopics)
}

const submit = handleSubmit(async (values) => {
  const outputData = await editor.save()
  const content = JSON.stringify(outputData)
  await props.submit({
    title: values.title,
    content,
    topics: values.topics.map((topic) => topic.id),
    bounty: bounty.value ?? 0,
  })
})
</script>

<style>
.ce-block__content,
.ce-toolbar__content {
  max-width: unset;
}

.ce-block__content {
  padding: 0;
}

.question-title-input {
  font-weight: bold;
}
.question-title-input .v-field__input,
.question-title-input .v-field-label {
  font-size: 1.5rem;
}

.question-topics {
  gap: 8px;
}

.question-topic-input {
  width: 240px;
}
</style>
