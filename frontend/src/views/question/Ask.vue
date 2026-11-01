<template>
  <AskView
    v-model:title="title"
    v-model:topics="topics"
    v-model:bounty="bounty"
    :title-props="titleProps"
    :editor-config="editorConfig"
    :topics-props="topicsProps"
    :search-topics="searchTopics"
    :create-topic="createTopic"
    :has-bounty="hasBounty"
    :bounty-props="bountyProps"
    :is-submitting="isSubmitting"
    :submit="submit"
    @create="onCreate"
    @remove-bounty="removeBounty"
    @add-bounty="addBounty"
  />
</template>

<script setup lang="ts">
import type EditorJS from '@editorjs/editorjs'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { defaultEditorConfig } from '@/utils/editor'
import { vuetifyConfig } from '@/utils/form'

import { useTopicSearch } from '@/composables/useTopicSearch'

import AskView from './AskView.vue'

import { QuestionApi } from '@/api/questions'
import { toast } from '@/lib/toast'
import { toTypedSchema } from '@/lib/zodTypedSchema'

let editor: EditorJS
const { t } = useI18n()
const editorConfig = defaultEditorConfig()
const router = useRouter()
const route = useRoute()

// 话题的搜索与新建从这一层取，交给只认 props 的选择器；组件自己不碰接口。
const { searchTopics, createTopic } = useTopicSearch()

const groupId = computed(() => route.query.groupId)

// watch(groupId, (groupId) => {
//   if (groupId) {
//     topics.value = [{ id: groupId as number, name: '' }]
//   }
// })

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

const submit = handleSubmit(async (values) => {
  const outputData = await editor.save()
  const content = JSON.stringify(outputData)
  const res = await QuestionApi.ask({
    title: values.title,
    content,
    type: 0,
    topics: values.topics.map((topic) => topic.id),
    bounty: bounty.value ?? 0,
  })
  toast.success(t('questions.ask.success'))
  router.push({ name: 'QuestionAnswerList', params: { questionId: res.id } })
})
</script>
