<template>
  <AskView
    :topic-items="items"
    :topic-loading="isLoading"
    :resolve-topics="resolveTopics"
    :submit="submit"
    @topic-search="search"
    @topic-focus="focus"
  />
</template>

<script setup lang="ts">
// 提问页（`/questions/ask`）：话题下拉的取数与提交这件事（调接口、报成功、跳走）都
// 在这儿；画的那一半（标题 / 正文 / 话题 / 悬赏这套表单与它的校验）在 `AskView.vue`。
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { useTopicSelector } from '@/composables/useTopicSelector'

import AskView from './AskView.vue'

import { QuestionApi } from '@/network/api/questions'

const router = useRouter()
const { t } = useI18n()

// 话题下拉那几件事（搜话题 / 建话题 / 把假项补建成真的）都在这儿。
const { items, isLoading, search, focus, resolveTopics } = useTopicSelector()

async function submit(payload: { title: string; content: string; topics: number[]; bounty: number }) {
  const res = await QuestionApi.ask({
    title: payload.title,
    content: payload.content,
    type: 0,
    topics: payload.topics,
    bounty: payload.bounty,
  })
  toast.success(t('questions.ask.success'))
  router.push({ name: 'QuestionAnswerList', params: { questionId: res.data.id } })
}
</script>
