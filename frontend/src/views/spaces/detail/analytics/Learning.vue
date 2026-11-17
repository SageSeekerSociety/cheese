<template>
  <LearningAnalysisView
    v-model:sub="learning.sub.value"
    :point-id="learning.pointId.value"
    :enabled="learning.enabled.value"
    :loading="learning.loading.value"
    :failed="learning.failed.value"
    :error-detail="learning.errorDetail.value"
    :overview="learning.pages.value.overview"
    :points="learning.pages.value.points"
    :point-detail="learning.pointDetail.value"
    :questions="learning.pages.value.questions"
    :words="learning.pages.value.words"
    :tasks="learning.pages.value.tasks"
    :behavior="learning.pages.value.behavior"
    :trend="learning.pages.value.trend"
    @open-point="learning.openPoint"
    @close-point="learning.closePoint"
    @open-excerpts="learning.openExcerpts"
    @export="learning.openExport"
    @ratios="learning.setRatios"
    @retry="learning.retry"
  >
    <template #legacy>
      <LearningView
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
  </LearningAnalysisView>
  <LearningExcerptDialog
    v-model:open="learning.excerptsOpen.value"
    :data="learning.excerpts.value"
    :loading="learning.excerptsLoading.value"
    :failed="learning.excerptsFailed.value"
    @open-source="learning.openSource"
  />
  <LearningExportDialog
    v-model:open="learning.exportOpen.value"
    :point="learning.exportPoint.value"
    :exporting="learning.exporting.value"
    @export="learning.doExport"
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

import LearningExcerptDialog from './components/LearningExcerptDialog.vue'
import LearningExportDialog from './components/LearningExportDialog.vue'
import { useLearningClass } from './composables/useLearningClass'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import { dedupeBlockIds } from './helpers'
import LearningAnalysisView from './LearningAnalysisView.vue'
import LearningView from './LearningView.vue'
import { buildAnalyticsApiParams, buildLearningQueueParams } from './utils'

import { SpacesApi } from '@/api/spaces'
import { toast } from '@/lib/toast'

const { t } = useI18n()
const { filters, replaceFilters, spaceId } = useSpaceAnalyticsFilters()
/** 学情分析（全班整体）那一半：七个子页签、知识点详情、摘录、导出。上面是原有的发言与队列。 */
const learning = useLearningClass(spaceId)

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

const knowledgePointModel = ref<number | null>(filters.value.knowledgePoint ?? null)

watch(filters, (value) => {
  knowledgePointModel.value = value.knowledgePoint ?? null
})

watch(knowledgePointModel, async () => {
  await replaceFilters({
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
