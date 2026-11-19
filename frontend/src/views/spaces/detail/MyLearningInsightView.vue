<script setup lang="ts">
// 「我的学习洞察」的画面：只有本人看得到的那一页（学情分析第 2 步）。
//
// 读写都不在这里 —— 容器 `MyLearningInsight.vue` 取数，把结果递进来，接住「标记不准」去办
// （场景规则见 docs/manual/dev/scenes.md）。组件预览站里也能单独看。
//
// 版式照可点原型 xueqing/demo.html 的 `vMe()`：左边一栏是「最该补的」一个知识点（一句话
// 归纳、依据摘录、标记不准）和「全部知识点」三档条；右边一栏是「这一页从哪来」和「我常问
// 的」。第 4 步加了三样：「最该补的」下面的「生成 3 道巩固题」「先看一段讲解」、没做完的
// 几组（「待做的巩固题」，老师推送的也在这里），以及右栏的「复习提醒」。做题的弹层在
// `LearningPracticeDialog.vue`，由容器摆。
//
// 四种状态各自一个样子，不混：读不出来（错误 + 重试）、这块板没开学情分析、开着但还没有
// 分析结果（空状态，右栏照样说清这一页会读什么）、有结果。
import type {
  LearningExplanation,
  LearningInsightPoint,
  LearningLevel,
  LearningPracticeSource,
  LearningQuestionKind,
  MyLearningInsight,
  MyLearningPractice,
} from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  /** 读回来的那一份；还没读到是 null。 */
  insight: MyLearningInsight | null
  loading: boolean
  /** 没读出来 —— 换成错误加一条重试的路，不画成「还没有结果」。 */
  failed: boolean
  /** 正在标「不准」的那条判断。 */
  disputing?: number | null
  /** 巩固练习一览（第 4 步）：没做完的几组、复习提醒、今天还能要几次。没读到是 null。 */
  practice?: MyLearningPractice | null
  /** 正在出题的知识点。 */
  starting?: number | null
  /** 「先看一段讲解」读回来的那一段，和正在读讲解的知识点。 */
  explanation?: LearningExplanation | null
  explaining?: number | null
}>()

const emit = defineEmits<{
  dispute: [judgmentId: number]
  retry: []
  /** 对一个知识点出一组（复习提醒里是 `review`）。 */
  practice: [pointId: number, source: Exclude<LearningPracticeSource, 'pushed'>]
  /** 打开没做完的一组。 */
  openPractice: [practiceId: number, pointId: number]
  explain: [pointId: number]
}>()

const { t, locale } = useI18n()

/** 三档条：几格亮。薄弱一格、不稳两格、掌握三格。 */
const FILLED: Record<LearningLevel, number> = { weak: 1, shaky: 2, mastered: 3 }

const enabled = computed(() => props.insight?.enabled === true)
const analysed = computed(() => props.insight?.updatedAt != null)
const points = computed(() => props.insight?.knowledgePoints ?? [])

const focus = computed<LearningInsightPoint | null>(() => {
  const id = props.insight?.focusKnowledgePointId
  if (id == null) return null
  return points.value.find((p) => p.id === id && p.judgment) ?? null
})

const tasks = computed(() => props.insight?.sources.tasks ?? [])

/** 题目名用读者语言的列举方式连起来（「第 3 题、第 4 题」/「Task 3 and Task 4」）。 */
const taskList = computed(() =>
  new Intl.ListFormat(locale.value, { style: 'short', type: 'conjunction' }).format(
    tasks.value.map((task) => t('spaces.learningAnalysis.insight.taskQuote', { name: task.name }))
  )
)

const lede = computed(() => {
  const insight = props.insight
  if (!insight || insight.updatedAt == null) return t('spaces.learningAnalysis.insight.ledeEmpty')
  return t('spaces.learningAnalysis.insight.lede', {
    tasks: tasks.value.length,
    messages: insight.sources.messagesRead,
    time: relTime(new Date(insight.updatedAt).toISOString()),
  })
})

function levelLabel(level: LearningLevel): string {
  return t(`spaces.learningAnalysis.insight.levels.${level}`)
}

function kindLabel(kind: LearningQuestionKind): string {
  return t(`spaces.learningAnalysis.insight.kinds.${kind}`)
}

const practiceLeft = computed(() => {
  const practice = props.practice
  return practice ? Math.max(practice.dailyLimit - practice.usedToday, 0) : null
})

const focusExplanation = computed(() =>
  props.explanation && focus.value && props.explanation.knowledgePointId === focus.value.id
    ? props.explanation.text
    : null
)

/** 平台日历的一天（YYYY-MM-DD）按读者语言写成「11月16日」/「11/16」。 */
function shortDate(day: string): string {
  const [year, month, date] = day.split('-').map(Number)
  return new Intl.DateTimeFormat(locale.value, { month: 'numeric', day: 'numeric' }).format(
    new Date(year, month - 1, date)
  )
}

function sourceLabel(source: LearningPracticeSource): string {
  return t(`spaces.learningAnalysis.practice.sources.${source}`)
}

const whoRows = computed(() => [
  { label: t('spaces.learningAnalysis.insight.whoYou'), value: t('spaces.learningAnalysis.insight.whoYouValue') },
  {
    label: t('spaces.learningAnalysis.insight.whoAdmins'),
    value: t('spaces.learningAnalysis.insight.whoAdminsValue'),
  },
  { label: t('spaces.learningAnalysis.insight.whoRaw'), value: t('spaces.learningAnalysis.insight.whoRawValue') },
])
</script>

<template>
  <PageHeader show-on-mobile :title="t('spaces.learningAnalysis.insight.title')" />

  <div class="mli">
    <BaseLoadError v-if="failed" @retry="emit('retry')" />

    <div v-else-if="loading && !insight" class="mli__loading">
      <v-progress-circular indeterminate color="primary" />
    </div>

    <BaseEmptyState
      v-else-if="!enabled"
      icon="mdi-chart-box-outline"
      :title="t('spaces.learningAnalysis.insight.off')"
      :desc="t('spaces.learningAnalysis.insight.offDesc')"
      data-testid="insight-off"
    />

    <template v-else>
      <p class="mli__lede">{{ lede }}</p>

      <div class="mli__cols">
        <div class="mli__main">
          <BaseEmptyState
            v-if="!analysed"
            class="mli__card"
            icon="mdi-weather-night"
            :title="t('spaces.learningAnalysis.insight.emptyTitle')"
            :desc="
              points.length
                ? t('spaces.learningAnalysis.insight.emptyDesc')
                : t('spaces.learningAnalysis.insight.noPoints')
            "
            data-testid="insight-empty"
          />

          <template v-else>
            <!-- 最该补的：一个知识点，带归纳、依据和「标记不准」。 -->
            <section class="mli__card" data-testid="insight-focus">
              <h2 class="mli__card-title">{{ t('spaces.learningAnalysis.insight.focusTitle') }}</h2>
              <template v-if="focus && focus.judgment">
                <h3 class="mli__focus-name">
                  <span data-user-content>{{ focus.name }}</span>
                  <span class="mli__level" :class="`mli__level--${focus.judgment.level}`">
                    {{ levelLabel(focus.judgment.level) }}
                  </span>
                </h3>
                <p class="mli__summary">{{ focus.judgment.summary }}</p>
                <div v-if="focus.judgment.evidence.length" class="mli__evidence">
                  <span class="mli__evidence-lead">{{ t('spaces.learningAnalysis.insight.evidenceLead') }}</span>
                  <span v-for="item in focus.judgment.evidence" :key="item.blockId" class="mli__quote">
                    {{ t('spaces.learningAnalysis.insight.evidenceIn', { task: item.taskName }) }}
                    <q data-user-content>{{ item.quote }}</q>
                  </span>
                </div>
                <p class="mli__note">
                  {{ t('spaces.learningAnalysis.insight.aiNote') }}
                  <BaseButton
                    kind="ghost"
                    size="sm"
                    :loading="disputing === focus.judgment.id"
                    :disabled="disputing != null"
                    data-testid="insight-dispute-focus"
                    @click="emit('dispute', focus.judgment.id)"
                  >
                    {{ t('spaces.learningAnalysis.insight.dispute') }}
                  </BaseButton>
                </p>
                <p v-if="focus.judgment.practice" class="mli__muted" data-testid="insight-focus-practice">
                  {{
                    t('spaces.learningAnalysis.practice.lastScore', {
                      correct: focus.judgment.practice.correct,
                      total: focus.judgment.practice.total,
                    })
                  }}
                </p>
                <div v-if="practice?.enabled" class="mli__actions">
                  <BaseButton
                    kind="primary"
                    size="sm"
                    :loading="starting === focus.id"
                    :disabled="starting != null || practiceLeft === 0"
                    data-testid="insight-practice"
                    @click="emit('practice', focus.id, 'self')"
                  >
                    {{ t('spaces.learningAnalysis.practice.start') }}
                  </BaseButton>
                  <BaseButton
                    kind="secondary"
                    size="sm"
                    :loading="explaining === focus.id"
                    :disabled="explaining != null"
                    data-testid="insight-explain"
                    @click="emit('explain', focus.id)"
                  >
                    {{ t('spaces.learningAnalysis.practice.explain') }}
                  </BaseButton>
                  <span v-if="practiceLeft !== null" class="mli__muted">
                    {{ t('spaces.learningAnalysis.practice.left', { n: practiceLeft }) }}
                  </span>
                </div>
                <p v-if="focusExplanation" class="mli__lecture" data-testid="insight-explanation">
                  <span data-user-content>{{ focusExplanation }}</span>
                </p>
              </template>
              <p v-else class="mli__muted">{{ t('spaces.learningAnalysis.insight.focusNone') }}</p>
            </section>

            <!-- Practice sets not finished yet, the ones a teacher pushed among them. -->
            <section v-if="practice?.open.length" class="mli__card" data-testid="insight-open-practice">
              <h2 class="mli__card-title">{{ t('spaces.learningAnalysis.practice.openTitle') }}</h2>
              <div v-for="item in practice.open" :key="item.id" class="mli__row" data-testid="insight-open-row">
                <span class="mli__row-label">
                  <span data-user-content>{{ item.knowledgePointName }}</span>
                  · {{ sourceLabel(item.source) }}
                </span>
                <span class="mli__muted">{{
                  item.ready
                    ? t('spaces.learningAnalysis.practice.progress', { answered: item.answered, total: item.total })
                    : t('spaces.learningAnalysis.practice.preparing')
                }}</span>
                <BaseButton
                  kind="ghost"
                  size="sm"
                  :loading="starting === item.knowledgePointId"
                  :disabled="starting != null"
                  @click="emit('openPractice', item.id, item.knowledgePointId)"
                >
                  {{ t('spaces.learningAnalysis.practice.go') }}
                </BaseButton>
              </div>
            </section>

            <!-- 全部知识点：清单顺序，每条一行三档条；没涉及的说一句。 -->
            <section class="mli__card" data-testid="insight-all">
              <h2 class="mli__card-title">{{ t('spaces.learningAnalysis.insight.allTitle') }}</h2>
              <ul class="mli__points">
                <li v-for="point in points" :key="point.id" class="mli__point" data-testid="insight-point">
                  <div class="mli__point-head">
                    <span class="mli__point-name" data-user-content>{{ point.name }}</span>
                    <template v-if="point.judgment">
                      <span class="mli__bar" :class="`mli__bar--${point.judgment.level}`" aria-hidden="true">
                        <span
                          v-for="n in 3"
                          :key="n"
                          class="mli__seg"
                          :class="{ 'mli__seg--on': n <= FILLED[point.judgment.level] }"
                        />
                      </span>
                      <span class="mli__level" :class="`mli__level--${point.judgment.level}`">
                        {{ levelLabel(point.judgment.level) }}
                      </span>
                    </template>
                    <span v-else class="mli__muted">{{ t('spaces.learningAnalysis.insight.untouched') }}</span>
                  </div>
                  <div v-if="point.judgment" class="mli__point-body">
                    <span class="mli__summary">{{ point.judgment.summary }}</span>
                    <span v-if="point.judgment.disputed" class="mli__disputed">
                      {{ t('spaces.learningAnalysis.insight.disputed') }}
                    </span>
                    <BaseButton
                      v-else
                      kind="ghost"
                      size="sm"
                      :loading="disputing === point.judgment.id"
                      :disabled="disputing != null"
                      data-testid="insight-dispute"
                      @click="emit('dispute', point.judgment.id)"
                    >
                      {{ t('spaces.learningAnalysis.insight.dispute') }}
                    </BaseButton>
                  </div>
                </li>
              </ul>
            </section>
          </template>
        </div>

        <aside class="mli__side">
          <!-- 这一页从哪来：读了什么、没读什么、谁看得到什么。 -->
          <section class="mli__card" data-testid="insight-sources">
            <h3 class="mli__card-title">{{ t('spaces.learningAnalysis.insight.sourceTitle') }}</h3>
            <p class="mli__muted">
              {{
                tasks.length
                  ? t('spaces.learningAnalysis.insight.sourceRead', { tasks: taskList })
                  : t('spaces.learningAnalysis.insight.sourceReadNone')
              }}
              <br />
              {{ t('spaces.learningAnalysis.insight.sourceNotRead') }}
            </p>
            <div v-for="row in whoRows" :key="row.label" class="mli__row">
              <span class="mli__row-label">{{ row.label }}</span>
              <v-chip size="small" variant="tonal">{{ row.value }}</v-chip>
            </div>
            <p class="mli__muted mli__foot">{{ t('spaces.learningAnalysis.insight.sourceFoot') }}</p>
          </section>

          <!-- Review reminders: what is due, or when the next one comes. -->
          <section v-if="practice?.enabled" class="mli__card" data-testid="insight-review">
            <h3 class="mli__card-title">{{ t('spaces.learningAnalysis.practice.reviewTitle') }}</h3>
            <template v-if="practice.due.length">
              <div
                v-for="item in practice.due"
                :key="item.knowledgePointId"
                class="mli__row"
                data-testid="insight-review-due"
              >
                <span class="mli__row-label">
                  <span data-user-content>{{ item.knowledgePointName }}</span>
                  <span class="mli__muted">
                    · {{ t('spaces.learningAnalysis.practice.dueOn', { date: shortDate(item.dueOn) }) }}</span
                  >
                </span>
                <BaseButton
                  kind="ghost"
                  size="sm"
                  :loading="starting === item.knowledgePointId"
                  :disabled="starting != null || practiceLeft === 0"
                  @click="emit('practice', item.knowledgePointId, 'review')"
                >
                  {{ t('spaces.learningAnalysis.practice.reviewAgain') }}
                </BaseButton>
              </div>
            </template>
            <p v-else-if="practice.upcoming" class="mli__muted">
              {{
                t('spaces.learningAnalysis.practice.reviewNext', {
                  date: shortDate(practice.upcoming.dueOn),
                  name: practice.upcoming.knowledgePointName,
                })
              }}
            </p>
            <p v-else class="mli__muted">{{ t('spaces.learningAnalysis.practice.reviewNone') }}</p>
          </section>

          <!-- 我常问的：按「知识点 × 提问类型」数次数。 -->
          <section class="mli__card" data-testid="insight-frequent">
            <h3 class="mli__card-title">{{ t('spaces.learningAnalysis.insight.frequentTitle') }}</h3>
            <p class="mli__muted">{{ t('spaces.learningAnalysis.insight.frequentHint') }}</p>
            <template v-if="insight?.frequentQuestions.length">
              <div
                v-for="item in insight.frequentQuestions"
                :key="`${item.knowledgePointId}-${item.kind}`"
                class="mli__row mli__row--stack"
                data-testid="insight-frequent-row"
              >
                <div class="mli__row-main">
                  <span class="mli__row-label">
                    <span data-user-content>{{
                      item.knowledgePointName ?? t('spaces.learningAnalysis.insight.otherPoint')
                    }}</span>
                    · {{ kindLabel(item.kind) }}
                  </span>
                  <span v-if="item.example" class="mli__muted mli__example" data-user-content>{{ item.example }}</span>
                </div>
                <v-chip size="small" variant="tonal">{{
                  t('spaces.learningAnalysis.insight.frequentCount', { n: item.count })
                }}</v-chip>
              </div>
            </template>
            <p v-else class="mli__muted">{{ t('spaces.learningAnalysis.insight.frequentEmpty') }}</p>
          </section>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.mli {
  padding: 4px 24px 32px;
}

.mli__loading {
  display: flex;
  padding: 48px;
  justify-content: center;
}

.mli__lede {
  margin: 0 0 16px;
  color: var(--muted);
}

.mli__cols {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

.mli__main,
.mli__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.mli__card {
  padding: 16px 20px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface);
}

.mli__card-title {
  margin: 0 0 10px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
}

.mli__focus-name {
  display: flex;
  margin: 0 0 6px;
  align-items: center;
  gap: 8px;
  color: var(--ink);
  font-size: 16px;
  font-weight: 600;
}

.mli__summary {
  margin: 0;
  color: var(--text);
}

.mli__evidence {
  display: flex;
  margin-top: 10px;
  padding: 8px 12px;
  flex-direction: column;
  gap: 4px;
  border-radius: 8px;
  background: var(--fill-2);
  color: var(--text);
  font-size: 13px;
}

.mli__evidence-lead {
  color: var(--muted);
}

.mli__note {
  display: flex;
  margin: 8px 0 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  color: var(--muted);
  font-size: 13px;
}

.mli__actions {
  display: flex;
  margin-top: 14px;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.mli__lecture {
  margin: 10px 0 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--fill-2);
  color: var(--text);
  font-size: 14px;
  white-space: pre-wrap;
}

.mli__muted {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.mli__level {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.mli__level--weak {
  background: var(--danger-wash);
  color: var(--danger-ink);
}

.mli__level--shaky {
  background: var(--warn-wash);
  color: var(--warn-ink);
}

.mli__level--mastered {
  background: var(--ok-wash);
  color: var(--ok-ink);
}

.mli__points {
  display: flex;
  margin: 0;
  padding: 0;
  flex-direction: column;
  list-style: none;
}

.mli__point {
  padding: 10px 0;
  border-top: 1px solid var(--line);
}

.mli__point:first-child {
  border-top: 0;
}

.mli__point-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.mli__point-name {
  flex: 1;
  min-width: 0;
  color: var(--ink);
  font-weight: 500;
}

.mli__point-body {
  display: flex;
  margin-top: 4px;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.mli__bar {
  display: inline-flex;
  gap: 3px;
}

.mli__seg {
  width: 22px;
  height: 6px;
  border-radius: 3px;
  background: var(--fill-2);
}

.mli__bar--weak .mli__seg--on {
  background: var(--danger);
}

.mli__bar--shaky .mli__seg--on {
  background: var(--warn);
}

.mli__bar--mastered .mli__seg--on {
  background: var(--ok);
}

.mli__disputed {
  color: var(--muted);
  font-style: italic;
}

.mli__row {
  display: flex;
  padding: 8px 0;
  align-items: center;
  gap: 8px;
  border-top: 1px solid var(--line);
}

.mli__row--stack {
  align-items: flex-start;
}

.mli__row-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.mli__row-label {
  flex: 1;
  color: var(--text);
}

.mli__example {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mli__foot {
  margin-top: 10px;
}

@media (max-width: 959.98px) {
  .mli {
    padding: 4px 16px 24px;
  }

  .mli__cols {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
