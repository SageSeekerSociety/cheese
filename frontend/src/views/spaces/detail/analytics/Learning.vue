<template>
  <div class="analytics-section">
    <div class="section-toolbar">
      <div>
        <h2 class="section-toolbar__title">学习</h2>
        <p class="section-toolbar__hint">成员与 AI 对话时卡住的地方，用来挑下一讲要讲的内容</p>
      </div>
    </div>

    <v-card flat rounded="lg" class="toolbar-card">
      <div class="toolbar-grid">
        <v-select
          v-model="studentModel"
          autocomplete="off"
          :items="studentItems"
          label="成员"
          density="comfortable"
          hide-details
          variant="outlined"
        />
        <v-select
          v-model="knowledgePointModel"
          autocomplete="off"
          :items="knowledgePointItems"
          label="知识点"
          density="comfortable"
          hide-details
          variant="outlined"
        />
      </div>

      <div v-if="learningFilters" class="toolbar-summary">{{ summary }}</div>
    </v-card>

    <v-progress-linear v-if="loading && !queues" indeterminate color="primary" class="mt-4" />

    <v-empty-state
      v-if="learningFilters && learningFilters.projectCount === 0"
      icon="mdi-account-search-outline"
      title="暂无可查看的成员项目"
      text="当前账号还读不到这门课下的成员项目，因此没有对话可以展示。"
    />

    <template v-else>
      <section class="learning-block">
        <div class="block-head">
          <h3 class="block-head__title">待处理队列</h3>
          <p class="block-head__hint">共性卡点按有多少个成员撞上排序</p>
        </div>

        <v-card v-if="queues" flat rounded="lg" class="queue-card">
          <div class="queue-card__title">成员标记「这道题我答不了」的问题</div>

          <template v-if="queues.reviewFlag.available">
            <LearningQuoteItem v-for="item in queues.reviewFlag.items" :key="item.blockId" :excerpt="item" />
            <p v-if="!queues.reviewFlag.items.length" class="queue-card__note">暂无成员标记的问题</p>
          </template>

          <template v-else>
            <p class="queue-card__note">暂无可显示的内容</p>
            <p class="queue-card__reason">{{ queues.reviewFlag.reason }}</p>
          </template>
        </v-card>

        <div v-if="queues?.stuckPoints.length" class="stuck-grid">
          <LearningStuckPointCard
            v-for="point in queues.stuckPoints"
            :key="point.knowledgePoint ?? '未归类'"
            :point="point"
            :checked="isSelected(point.example.blockId)"
            @update:checked="(value) => setSelected(point.example.blockId, value)"
          />
        </div>

        <v-empty-state
          v-else-if="queues"
          icon="mdi-check-circle-outline"
          title="暂无共性卡点"
          text="当前筛选范围下没有成员卡在同一处。"
        />
      </section>

      <section class="learning-block">
        <div class="block-head">
          <h3 class="block-head__title">成员发言</h3>
          <p class="block-head__hint">共 {{ questions.length }} 条，勾选要带进提纲的</p>
        </div>

        <v-card v-if="questions.length" flat rounded="lg" class="quote-card">
          <LearningQuoteItem
            v-for="question in questions"
            :key="question.blockId"
            selectable
            :excerpt="question"
            :checked="isSelected(question.blockId)"
            @update:checked="(value) => setSelected(question.blockId, value)"
          />
        </v-card>

        <v-empty-state
          v-else-if="!loading"
          icon="mdi-comment-outline"
          title="暂无成员发言"
          text="当前筛选范围下没有成员的发言记录。"
        />
      </section>

      <v-card flat rounded="lg" class="outline-actions">
        <span class="outline-actions__count">已选 {{ selected.length }} 条发言</span>
        <div class="outline-actions__buttons">
          <v-btn v-if="selected.length" variant="text" rounded="lg" @click="clearSelection">清空</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            rounded="lg"
            :loading="outlineLoading"
            :disabled="!selected.length"
            @click="buildOutline"
          >
            生成讲解提纲
          </v-btn>
        </div>
      </v-card>

      <section v-if="outline" class="learning-block">
        <div class="block-head">
          <h3 class="block-head__title">{{ outline.title }}</h3>
          <p class="block-head__hint">讲次按勾选顺序排列</p>
        </div>

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
import { toast } from 'vuetify-sonner'

import LearningOutlineCard from './components/LearningOutlineCard.vue'
import LearningQuoteItem from './components/LearningQuoteItem.vue'
import LearningStuckPointCard from './components/LearningStuckPointCard.vue'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import { dedupeBlockIds, formatCount } from './helpers'
import { buildAnalyticsApiParams, buildLearningQueueParams } from './utils'

import { SpacesApi } from '@/network/api/spaces'

const { filters, replaceFilters, spaceId } = useSpaceAnalyticsFilters()

const loading = ref(false)
const outlineLoading = ref(false)
const learningFilters = ref<SpaceLearningFilters | null>(null)
const queues = ref<SpaceLearningQueues | null>(null)
const questions = ref<SpaceLearningQuestion[]>([])
const outline = ref<SpaceLearningOutline | null>(null)

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
  { title: '全部成员', value: null },
  ...(learningFilters.value?.students ?? []).map((student) => ({ title: student.name, value: student.handle })),
])

const knowledgePointItems = computed(() => [
  { title: '全部知识点', value: null },
  ...(learningFilters.value?.knowledgePoints ?? []).map((point) => ({ title: point.name, value: point.categoryId })),
])

const summary = computed(() => {
  if (!learningFilters.value) return ''

  return [
    `可查看的成员项目 ${formatCount(learningFilters.value.projectCount)} 个`,
    `成员 ${formatCount(learningFilters.value.students.length)} 人`,
    `发言 ${formatCount(questions.value.length)} 条`,
    `共性卡点 ${formatCount(queues.value?.stuckPoints.length ?? 0)} 个`,
  ].join(' · ')
})

const loadFilters = async () => {
  try {
    const { data } = await SpacesApi.getLearningFilters(spaceId.value)
    learningFilters.value = data
  } catch (error) {
    console.error('load learning filters failed', error)
    toast.error('加载学习筛选条件失败')
  }
}

const loadQueues = async () => {
  try {
    const { data } = await SpacesApi.getLearningQueues(spaceId.value, buildLearningQueueParams(filters.value))
    queues.value = data
  } catch (error) {
    console.error('load learning queues failed', error)
    toast.error('加载待处理队列失败')
  }
}

const loadQuestions = async () => {
  try {
    const { data } = await SpacesApi.getLearningQuestions(
      spaceId.value,
      buildAnalyticsApiParams('learning', filters.value)
    )
    questions.value = data.questions
  } catch (error) {
    console.error('load learning questions failed', error)
    toast.error('加载成员发言失败')
  }
}

const load = async () => {
  loading.value = true
  try {
    await Promise.all([loadQueues(), loadQuestions()])
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
    toast.error('生成讲解提纲失败')
  } finally {
    outlineLoading.value = false
  }
}
</script>

<style scoped lang="scss">
.analytics-section {
  display: flex;
  flex-direction: column;
}

.section-toolbar {
  margin-bottom: 16px;
}

.section-toolbar__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

.section-toolbar__hint {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.toolbar-card {
  padding: 16px;
  background-color: rgba(var(--v-theme-surface), 1);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.toolbar-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.toolbar-summary {
  margin-top: 12px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.learning-block {
  margin-top: 24px;
}

.block-head {
  margin-bottom: 12px;
}

.block-head__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

.block-head__hint {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.queue-card {
  padding: 16px;
  background-color: rgba(var(--v-theme-surface), 1);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.queue-card__title {
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--text);
}

.queue-card__note {
  margin: 8px 0 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
}

.queue-card__reason {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--faint);
}

.stuck-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.quote-card {
  padding: 4px 16px;
  background-color: rgba(var(--v-theme-surface), 1);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.outline-actions {
  display: flex;
  padding: 16px;
  margin-top: 24px;
  background-color: rgba(var(--v-theme-surface), 1);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  gap: 16px;
  justify-content: space-between;
  align-items: center;
}

.outline-actions__count {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.outline-actions__buttons {
  display: flex;
  gap: 8px;
  align-items: center;
}

@media (max-width: 960px) {
  .toolbar-grid,
  .stuck-grid {
    grid-template-columns: 1fr;
  }

  .outline-actions {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
