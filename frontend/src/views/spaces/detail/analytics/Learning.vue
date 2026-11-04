<template>
  <LearningView
    v-model:student="studentModel"
    v-model:knowledge-point="knowledgePointModel"
    :loading="loading"
    :outline-loading="outlineLoading"
    :learning-filters="learningFilters"
    :queues="queues"
    :questions="questions"
    :outline="outline"
    :failed="failed"
    :error-detail="errorDetail"
    :selected="selected"
    @load="load"
    @set-selected="setSelected"
    @clear-selection="clearSelection"
    @build-outline="buildOutline"
  />
</template>

<script setup lang="ts">
import type {
  SpaceLearningFilters,
  SpaceLearningOutline,
  SpaceLearningQuestion,
  SpaceLearningQueues,
} from '@/types/apiSpaces'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import { dedupeBlockIds } from './helpers'
import LearningView from './LearningView.vue'
import { buildAnalyticsApiParams, buildLearningQueueParams } from './utils'

import { SpacesApi } from '@/api/spaces'
import { toast } from '@/lib/toast'

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

//: 勾中的发言。顺序就是提纲里的讲次顺序，所以只往后加、不重排。
const selected = ref<string[]>([])

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
    const data = await SpacesApi.getLearningFilters(spaceId.value)
    learningFilters.value = data
  } catch (error) {
    console.error('load learning filters failed', error)
    toast.error(t('spaces.analytics.learning.toast.filtersFailed'))
  }
}

const loadQueues = async () => {
  const data = await SpacesApi.getLearningQueues(spaceId.value, buildLearningQueueParams(filters.value))
  queues.value = data
}

const loadQuestions = async () => {
  const data = await SpacesApi.getLearningQuestions(spaceId.value, buildAnalyticsApiParams('learning', filters.value))
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

const setSelected = (blockId: string, value: boolean) => {
  if (value) {
    if (!selected.value.includes(blockId)) {
      selected.value = [...selected.value, blockId]
    }
    return
  }

  selected.value = selected.value.filter((id) => id !== blockId)
}

const clearSelection = () => {
  selected.value = []
}

const buildOutline = async () => {
  const blockIds = dedupeBlockIds(selected.value)
  if (!blockIds.length) return

  outlineLoading.value = true
  try {
    const data = await SpacesApi.buildLearningOutline(spaceId.value, { blockIds })
    outline.value = data
  } catch (error) {
    console.error('build learning outline failed', error)
    toast.error(t('spaces.analytics.learning.toast.outlineFailed'))
  } finally {
    outlineLoading.value = false
  }
}
</script>
