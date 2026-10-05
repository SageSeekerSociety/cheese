<template>
  <LearningView
    v-model:student="studentModel"
    v-model:knowledge-point="knowledgePointModel"
    :learning-filters="learningFilters"
    :queues="queues"
    :questions="questions"
    :outline="outline"
    :loading="loading"
    :outline-loading="outlineLoading"
    :failed="failed"
    :error-detail="errorDetail"
    @retry="load"
    @build-outline="buildOutline"
  />
</template>

<script setup lang="ts">
// 学习这一格的容器：读学生/知识点筛选、拉队列与发言、把筛选写回地址、生成提纲。画面
// 在 `LearningView.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type {
  SpaceLearningFilters,
  SpaceLearningOutline,
  SpaceLearningQuestion,
  SpaceLearningQueues,
} from '@/network/api/spaces/types'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import LearningView from './LearningView.vue'
import { buildAnalyticsApiParams, buildLearningQueueParams } from './utils'

import { SpacesApi } from '@/network/api/spaces'

const { t } = useI18n()
const { filters, replaceFilters, spaceId } = useSpaceAnalyticsFilters()

const loading = ref(false)
const outlineLoading = ref(false)
const learningFilters = ref<SpaceLearningFilters | null>(null)
const queues = ref<SpaceLearningQueues | null>(null)
const questions = ref<SpaceLearningQuestion[]>([])
const outline = ref<SpaceLearningOutline | null>(null)

//: 队列 + 发言没读到时的长相：替换掉内容，就地给原因和一条重试的路。
const failed = ref(false)
const errorDetail = ref<string | null>(null)

const studentModel = ref<string | null>(filters.value.student ?? null)
const knowledgePointModel = ref<number | null>(filters.value.knowledgePoint ?? null)

watch(filters, (value) => {
  studentModel.value = value.student ?? null
  knowledgePointModel.value = value.knowledgePoint ?? null
})

watch([studentModel, knowledgePointModel], async () => {
  await replaceFilters({
    student: studentModel.value ?? undefined,
    knowledgePoint: knowledgePointModel.value ?? undefined,
  })
})

const loadFilters = async () => {
  try {
    const { data } = await SpacesApi.getLearningFilters(spaceId.value)
    learningFilters.value = data
  } catch (error) {
    console.error('load learning filters failed', error)
    toast.error(t('spaces.analytics.learning.toast.filtersFailed'))
  }
}

const loadQueues = async () => {
  const { data } = await SpacesApi.getLearningQueues(spaceId.value, buildLearningQueueParams(filters.value))
  queues.value = data
}

const loadQuestions = async () => {
  const { data } = await SpacesApi.getLearningQuestions(
    spaceId.value,
    buildAnalyticsApiParams('learning', filters.value)
  )
  questions.value = data.questions
}

const load = async () => {
  loading.value = true
  failed.value = false
  errorDetail.value = null
  try {
    await Promise.all([loadQueues(), loadQuestions()])
  } catch (error) {
    failed.value = true
    errorDetail.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loading.value = false
  }
}

watch(
  spaceId,
  (value) => {
    if (value) {
      loadFilters().catch(() => undefined)
    }
  },
  { immediate: true }
)

watch(
  filters,
  () => {
    load().catch(() => undefined)
  },
  { immediate: true }
)

const buildOutline = async (blockIds: string[]) => {
  if (!blockIds.length) return

  outlineLoading.value = true
  try {
    const { data } = await SpacesApi.buildLearningOutline(spaceId.value, { blockIds })
    outline.value = data
  } catch (error) {
    console.error('build learning outline failed', error)
    toast.error(t('spaces.analytics.learning.toast.outlineFailed'))
  } finally {
    outlineLoading.value = false
  }
}
</script>
