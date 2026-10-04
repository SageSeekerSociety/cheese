<template>
  <div class="an-section">
    <p class="an-note">{{ t('spaces.analytics.learning.lede') }}</p>

    <div class="an-bar">
      <v-select
        v-model="studentModel"
        autocomplete="off"
        :items="studentItems"
        :prefix="t('spaces.analytics.learning.student')"
        :aria-label="t('spaces.analytics.learning.student')"
        density="compact"
        hide-details
        variant="outlined"
      />
      <v-select
        v-model="knowledgePointModel"
        autocomplete="off"
        :items="knowledgePointItems"
        :prefix="t('spaces.analytics.learning.knowledgePoint')"
        :aria-label="t('spaces.analytics.learning.knowledgePoint')"
        density="compact"
        hide-details
        variant="outlined"
      />
      <span v-if="learningFilters" class="lr__summary">{{ summary }}</span>
    </div>

    <v-progress-linear v-if="loading && !queues" indeterminate color="primary" />

    <BaseEmptyState
      v-if="learningFilters && learningFilters.projectCount === 0"
      size="inline"
      :title="t('spaces.analytics.learning.noProjects')"
    />

    <!-- The queues and messages are this block's whole content; if they failed to load, replace them with the reason and a way to retry. -->
    <BaseLoadError
      v-else-if="failed"
      :title="t('spaces.analytics.learning.loadFailed')"
      :error="errorDetail"
      @retry="load"
    />

    <template v-else>
      <section class="lr__block">
        <h3 class="an-card__title">{{ t('spaces.analytics.learning.queue.title') }}</h3>
        <p class="lr__hint">{{ t('spaces.analytics.learning.queue.hint') }}</p>

        <div v-if="queues" class="an-card">
          <div class="lr__card-title">{{ t('spaces.analytics.learning.queue.flagged') }}</div>

          <template v-if="queues.reviewFlag.available">
            <LearningQuoteItem v-for="item in queues.reviewFlag.items" :key="item.blockId" :excerpt="item" />
            <BaseEmptyState
              v-if="!queues.reviewFlag.items.length"
              size="inline"
              class="lr__gap"
              :title="t('spaces.analytics.learning.queue.noFlagged')"
            />
          </template>

          <template v-else>
            <p class="an-note lr__gap">{{ t('spaces.analytics.learning.queue.unavailable') }}</p>
            <p class="lr__reason">{{ queues.reviewFlag.reason }}</p>
          </template>
        </div>

        <div v-if="queues?.stuckPoints.length" class="an-grid">
          <LearningStuckPointCard
            v-for="point in queues.stuckPoints"
            :key="point.knowledgePoint ?? '-'"
            :point="point"
            :checked="isSelected(point.example.blockId)"
            @update:checked="(value) => setSelected(point.example.blockId, value)"
          />
        </div>
        <BaseEmptyState v-else-if="queues" size="inline" :title="t('spaces.analytics.learning.queue.noStuck')" />
      </section>

      <section class="lr__block">
        <h3 class="an-card__title">{{ t('spaces.analytics.learning.questions.title') }}</h3>
        <p class="lr__hint">{{ t('spaces.analytics.learning.questions.hint', { n: questions.length }) }}</p>

        <div v-if="questions.length" class="an-card lr__quotes">
          <LearningQuoteItem
            v-for="question in questions"
            :key="question.blockId"
            selectable
            :excerpt="question"
            :checked="isSelected(question.blockId)"
            @update:checked="(value) => setSelected(question.blockId, value)"
          />
        </div>
        <BaseEmptyState v-else-if="!loading" size="inline" :title="t('spaces.analytics.learning.questions.empty')" />
      </section>

      <div class="an-card lr__actions">
        <span class="lr__count">{{ t('spaces.analytics.learning.selected', { n: selected.length }) }}</span>
        <div class="lr__buttons">
          <BaseButton v-if="selected.length" kind="ghost" @click="clearSelection">
            {{ t('spaces.analytics.learning.clear') }}
          </BaseButton>
          <BaseButton kind="primary" :loading="outlineLoading" :disabled="!selected.length" @click="buildOutline">
            {{ t('spaces.analytics.learning.buildOutline') }}
          </BaseButton>
        </div>
      </div>

      <section v-if="outline" class="lr__block">
        <h3 class="an-card__title">{{ outline.title }}</h3>
        <p class="lr__hint">{{ t('spaces.analytics.learning.outlineHint') }}</p>
        <LearningOutlineCard :outline="outline" />
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import type {
  SpaceLearningFilters,
  SpaceLearningOutline,
  SpaceLearningQuestion,
  SpaceLearningQueues,
} from '@/network/api/spaces/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import LearningOutlineCard from './components/LearningOutlineCard.vue'
import LearningQuoteItem from './components/LearningQuoteItem.vue'
import LearningStuckPointCard from './components/LearningStuckPointCard.vue'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import { dedupeBlockIds, formatCount } from './helpers'
import { buildAnalyticsApiParams, buildLearningQueueParams } from './utils'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
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

const studentItems = computed(() => [
  { title: t('spaces.analytics.learning.allStudents'), value: null },
  ...(learningFilters.value?.students ?? []).map((student) => ({ title: student.name, value: student.handle })),
])

const knowledgePointItems = computed(() => [
  { title: t('spaces.analytics.learning.allKnowledgePoints'), value: null },
  ...(learningFilters.value?.knowledgePoints ?? []).map((point) => ({ title: point.name, value: point.categoryId })),
])

const summary = computed(() => {
  if (!learningFilters.value) return ''

  return t('spaces.analytics.learning.summary', {
    projects: formatCount(learningFilters.value.projectCount),
    students: formatCount(learningFilters.value.students.length),
    questions: formatCount(questions.value.length),
    stuck: formatCount(queues.value?.stuckPoints.length ?? 0),
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

const isSelected = (blockId: string) => selected.value.includes(blockId)

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

<style scoped src="./analytics.css"></style>

<style scoped>
.lr__summary {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}

.lr__block {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 8px;
}

.lr__block > .an-card__title {
  margin: 0;
}

.lr__hint {
  margin: -8px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.lr__card-title {
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.lr__gap {
  margin-top: 8px;
}

.lr__reason {
  margin: 8px 0 0;
  color: var(--faint);
  font-size: 13px;
  line-height: var(--lh-13);
}

.lr__quotes {
  padding-block: 4px;
}

.lr__actions {
  display: flex;
  gap: 16px;
  justify-content: space-between;
  align-items: center;
}

.lr__count {
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
}

.lr__buttons {
  display: flex;
  gap: 8px;
  align-items: center;
}
</style>
