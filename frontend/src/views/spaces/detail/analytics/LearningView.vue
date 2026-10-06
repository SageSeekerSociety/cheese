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
      @retry="emit('retry')"
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
          <BaseButton kind="primary" :loading="outlineLoading" :disabled="!selected.length" @click="onBuildOutline">
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
// 学习这一格的画面：学生/知识点两个筛选、队列与发言、勾选和「生成提纲」。读数和写
// 地址归页面 `Learning.vue`（场景规则见 docs/manual/dev/scenes.md）。勾选是这一格
// 自己的画面状态，留在画面里；要生成提纲时把选中的 blockId 交给页面去办。
import type {
  SpaceLearningFilters,
  SpaceLearningOutline,
  SpaceLearningQuestion,
  SpaceLearningQueues,
} from '@/network/api/spaces/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import LearningOutlineCard from './components/LearningOutlineCard.vue'
import LearningQuoteItem from './components/LearningQuoteItem.vue'
import LearningStuckPointCard from './components/LearningStuckPointCard.vue'
import { dedupeBlockIds, formatCount } from './helpers'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'

const props = defineProps<{
  learningFilters: SpaceLearningFilters | null
  queues: SpaceLearningQueues | null
  questions: SpaceLearningQuestion[]
  outline: SpaceLearningOutline | null
  loading: boolean
  outlineLoading: boolean
  failed: boolean
  errorDetail: string | null
}>()

const studentModel = defineModel<string | null>('student', { required: true })
const knowledgePointModel = defineModel<number | null>('knowledgePoint', { required: true })

const emit = defineEmits<{
  retry: []
  buildOutline: [blockIds: string[]]
}>()

const { t } = useI18n()

//: 勾中的发言。顺序就是提纲里的讲次顺序，所以只往后加、不重排。
const selected = ref<string[]>([])

const studentItems = computed(() => [
  { title: t('spaces.analytics.learning.allStudents'), value: null },
  ...(props.learningFilters?.students ?? []).map((student) => ({ title: student.name, value: student.handle })),
])

const knowledgePointItems = computed(() => [
  { title: t('spaces.analytics.learning.allKnowledgePoints'), value: null },
  ...(props.learningFilters?.knowledgePoints ?? []).map((point) => ({ title: point.name, value: point.categoryId })),
])

const summary = computed(() => {
  if (!props.learningFilters) return ''

  return t('spaces.analytics.learning.summary', {
    projects: formatCount(props.learningFilters.projectCount),
    students: formatCount(props.learningFilters.students.length),
    questions: formatCount(props.questions.length),
    stuck: formatCount(props.queues?.stuckPoints.length ?? 0),
  })
})

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

const onBuildOutline = () => {
  const blockIds = dedupeBlockIds(selected.value)
  if (!blockIds.length) return
  emit('buildOutline', blockIds)
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
