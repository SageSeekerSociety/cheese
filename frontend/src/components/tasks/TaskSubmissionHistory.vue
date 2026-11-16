<template>
  <div class="d-flex flex-column ga-2 w-100 align-stretch">
    <v-card v-if="submissions.length" flat rounded="lg" :class="{ border: outlined, 'mb-4': !isDialog }">
      <template v-if="!hideTitle" #title> {{ title || t('tasks.submissionHistory.latest') }} </template>
      <template #text>
        <v-card border flat rounded="lg" class="pa-4" :class="{ 'gradient-card': highlightLatest }">
          <div class="d-flex flex-row align-center mb-2">
            <v-chip color="primary" variant="tonal" size="small"> #{{ latestSubmission.version }} </v-chip>
            <span class="ml-2">
              {{
                t('tasks.submissionHistory.submittedAt', {
                  time: dayjs(latestSubmission.createdAt).format('YYYY-MM-DD HH:mm'),
                })
              }}
            </span>
            <div class="flex-grow-1"></div>
            <v-chip
              v-if="latestSubmission.review"
              size="small"
              variant="tonal"
              :color="calcSubmissionReviewColor(latestSubmission.review)"
            >
              {{ calcSubmissionReviewText(latestSubmission.review) }}
            </v-chip>
          </div>
          <div v-for="(content, index) in latestSubmission.content" :key="index">
            <SubmissionContentCard
              :content="content"
              :class="{ 'mb-2': index !== latestSubmission.content.length - 1 }"
            />
          </div>
        </v-card>

        <!-- 额度：这份领题在这道题上用掉多少。题目按额度计分时这一块还带那一行的分，
             以及它怎么算的。学生看不到评分要点时这里只剩用量。 -->
        <div v-if="usage" class="tsh__usage mt-4">
          <div class="tsh__eyebrow">
            {{
              t(
                reviewable
                  ? 'tasks.submissionHistory.usageEyebrowTeacher'
                  : 'tasks.submissionHistory.usageEyebrowStudent'
              )
            }}
          </div>
          <div class="tsh__usage-top">
            <span class="tsh__big t-num">{{ fmtNum(usage.credits) }}</span>
            <span class="tsh__meta">{{ t('tasks.submissionHistory.creditsUnit') }}</span>
            <v-chip v-if="usageChip" size="small" variant="tonal" color="primary">{{ usageChip }}</v-chip>
            <span v-if="usage.rank" class="tsh__meta">
              {{ t('tasks.submissionHistory.usageRank', { rank: usage.rank, ranked: usage.ranked ?? 0 }) }}
            </span>
          </div>
          <div class="tsh__meta">{{ usageRuleText }}</div>
          <details v-if="usage.models?.length" class="tsh__more">
            <summary>{{ t('tasks.submissionHistory.usageByModel') }}</summary>
            <div v-for="m in usage.models" :key="m.model" class="tsh__kv">
              <span>{{ m.model || t('tasks.submissionHistory.usageUnknownModel') }}</span>
              <span class="t-num">{{ fmtNum(m.credits) }}</span>
            </div>
          </details>
        </div>

        <v-card v-if="reviewable" flat rounded="lg" border class="mt-4">
          <template #title>
            {{
              latestSubmission.review?.reviewed
                ? t('tasks.submissionHistory.editReview')
                : t('tasks.submissionHistory.review')
            }}
          </template>
          <template #text>
            <v-form @submit.prevent="submitReview">
              <v-radio-group
                v-model="accepted"
                inline
                :label="t('tasks.submissionHistory.passed')"
                v-bind="acceptedProps"
              >
                <v-radio :label="t('tasks.submissionHistory.accept')" :value="true"></v-radio>
                <v-radio :label="t('tasks.submissionHistory.reject')" :value="false"></v-radio>
              </v-radio-group>
              <!-- A score is what a pass is worth (the result reads 「已通过 N 分」); a
                   rejection reads 「已驳回」 with no number, so it asks for none. When
                   the rubric gives this version's stage a usage line, the score here
                   is the manual items only — the backend adds the usage line's own
                   score — so the label says so and the cap is that stage's manual
                   total, not 100. -->
              <BaseField
                v-if="accepted !== false"
                :label="
                  manualCap == null
                    ? t('tasks.submissionHistory.score')
                    : t('tasks.submissionHistory.manualScore', { max: manualCap })
                "
                required
                :error="scoreError"
                class="mb-4"
              >
                <template #default="{ id, describedby, invalid, required }">
                  <v-text-field
                    :id="id"
                    :model-value="score"
                    :aria-describedby="describedby"
                    :aria-invalid="invalid"
                    :aria-required="required"
                    :error="invalid"
                    type="number"
                    min="0"
                    :max="scoreMax"
                    variant="outlined"
                    density="comfortable"
                    hide-details
                    @update:model-value="score = $event === '' ? undefined : Number($event)"
                  />
                </template>
              </BaseField>

              <!-- 手动项 + 额度那一行的账：这一版通过之后看到的每一部分各得几分。 -->
              <div v-if="manualCap != null" class="tsh__score mb-4">
                <span>{{ t('tasks.submissionHistory.manualPart') }}</span>
                <span class="t-num"> {{ score == null ? '—' : score }} / {{ manualCap }} </span>
                <span>{{ t('tasks.submissionHistory.autoPart') }}</span>
                <span class="t-num">{{ autoLineText }}</span>
                <span class="tsh__total-label">{{ t('tasks.submissionHistory.totalPart') }}</span>
                <span class="tsh__total-label t-num">{{ totalPreview }}</span>
              </div>
              <p v-if="manualCap != null" class="tsh__meta mb-4">
                {{ usageModeHint }}
              </p>
              <v-textarea
                v-model="comment"
                autocomplete="off"
                :label="t('tasks.submissionHistory.comment')"
                v-bind="commentProps"
              />
            </v-form>
          </template>
          <template #actions>
            <BaseButton v-if="latestSubmission.review?.reviewed" kind="danger" @click="cancelReview">
              {{ t('tasks.submissionHistory.cancelReview') }}
            </BaseButton>
            <BaseButton kind="primary" :loading="isSubmitting" :disabled="isSubmitting" @click="submitReview">
              {{ t('tasks.submissionHistory.submit') }}
            </BaseButton>
          </template>
        </v-card>
        <SubmissionReviewStatus v-else-if="latestSubmission.review" class="mt-4" :review="latestSubmission.review" />
        <!-- 判过之后：这个总分是怎么来的。手动几项 + 额度那一行各得几分，合计就是上面
             那个「已通过 N 分」。 -->
        <div v-if="!reviewable && autoScores" class="tsh__score mt-2">
          <span>{{ t('tasks.submissionHistory.manualPart') }}</span>
          <span class="t-num">{{ savedManualText }}</span>
          <span>{{ t('tasks.submissionHistory.autoPart') }}</span>
          <span class="t-num">{{ autoLineText }}</span>
          <span class="tsh__total-label">{{ t('tasks.submissionHistory.totalPart') }}</span>
          <span class="tsh__total-label t-num">{{ savedTotalText }}</span>
        </div>
      </template>
    </v-card>

    <v-card v-if="showHistory" flat rounded="lg" :class="{ border: outlined }">
      <template v-if="submissions.length && !hideHistoryTitle" #title>
        {{ historyTitle || t('tasks.submissionHistory.history') }}
      </template>
      <infinite-scroll
        :has-more="hasMore"
        :loading="loadingMore"
        :initial-loading="refreshing"
        :is-empty="submissions.length <= 1"
        :shown="submissions.length"
        :total="total"
        force-manual
        @load-more="loadMore"
      >
        <template #empty>
          <BaseEmptyState size="compact" icon="" :title="emptyText || t('tasks.submissionHistory.empty')" />
        </template>
        <v-expansion-panels>
          <template v-for="submission in submissions.slice(1)" :key="submission.id">
            <v-expansion-panel :elevation="0" ripple>
              <template #title>
                <div class="d-flex flex-row align-center">
                  <v-chip color="primary" variant="tonal" size="small"> #{{ submission.version }} </v-chip>
                  <span class="ml-2">
                    {{
                      t('tasks.submissionHistory.submittedBy', {
                        name: submission.submitter.nickname,
                        time: dayjs(submission.createdAt).format('YYYY-MM-DD HH:mm'),
                      })
                    }}
                  </span>
                  <div class="flex-grow-1"></div>
                  <v-chip
                    v-if="submission.review"
                    size="small"
                    variant="tonal"
                    :color="calcSubmissionReviewColor(submission.review)"
                  >
                    {{ calcSubmissionReviewText(submission.review) }}
                  </v-chip>
                </div>
              </template>
              <template #text>
                <div v-for="(content, index) in submission.content" :key="index">
                  <SubmissionContentCard
                    :content="content"
                    :class="{ 'mb-2': index !== submission.content.length - 1 }"
                  />
                </div>
                <SubmissionReviewStatus v-if="submission.review" class="mt-4" :review="submission.review" />
              </template>
            </v-expansion-panel>
          </template>
        </v-expansion-panels>
      </infinite-scroll>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import type { TaskRubricLine, TaskSubmissionReview } from '@/types'

import { computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import SubmissionContentCard from './SubmissionContentCard.vue'
import SubmissionReviewStatus from './SubmissionReviewStatus.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseField from '@/components/base/BaseField.vue'
import InfiniteScroll from '@/components/common/InfiniteScroll.vue'
import { fmtNum } from '@/lib/usageFormat'
import { toTypedSchema } from '@/lib/zodTypedSchema'
import { useTaskSubmissionHistory } from '@/views/tasks/composables'

const { t } = useI18n()

/** `changed`：这一版的评审写下、改了或撤了。外面别处也显示这一版判没判（领取者的表、我的进度），
 *  它们听这一声重读。 */
const emit = defineEmits<{ changed: [] }>()

interface Props {
  taskId: number
  participantId: number
  reviewable?: boolean
  showHistory?: boolean
  hideTitle?: boolean
  hideHistoryTitle?: boolean
  title?: string
  historyTitle?: string
  emptyText?: string
  outlined?: boolean
  highlightLatest?: boolean
  isDialog?: boolean
  /** 只看这个阶段（阶段的 id）的提交；`null` = 不分阶段。 */
  stage?: string | null
  /** 这道题的评分要点；题目按额度计分时用它算这版评审手动项的上限。 `null` = 没有
   *  （不分阶段的老题、或者调用方没传）：这时按老规矩 0–100。 */
  rubric?: TaskRubricLine[] | null
}

const props = withDefaults(defineProps<Props>(), {
  reviewable: false,
  showHistory: true,
  hideTitle: false,
  hideHistoryTitle: false,
  title: '',
  historyTitle: '',
  emptyText: '',
  outlined: false,
  highlightLatest: false,
  isDialog: false,
  stage: null,
  rubric: null,
})

// 取数与写评审都在这个 composable 里：组件不许够到 API 层，只收 props、发 `changed`。
// 切题/切领取者时外面会重读（`props.taskId` 变了），它每次现取一次。
const {
  submissions,
  latestSubmission,
  usage,
  refresh,
  loadMore,
  hasMore,
  refreshing,
  loadingMore,
  total,
  submitReview: saveReview,
  cancelReview,
} = useTaskSubmissionHistory(
  { taskId: () => props.taskId, participantId: () => props.participantId, stage: () => props.stage },
  () => emit('changed')
)

// ── 额度那一段 ────────────────────────────────────────────────────────────────

/** 额度那一行的规则怎么说。学生的界面上一律说「评分要点没有公开」时另外处理。 */
const usageRuleText = computed(() => {
  const line = usage.value?.line
  if (!line) return ''
  if (line.mode === 'rank') return t('tasks.submissionHistory.usageRuleRank')
  return t('tasks.submissionHistory.usageRuleThreshold', { full: line.full ?? 0, zero: line.zero ?? 0 })
})

/** 额度那一行的分：分数 + 满分，排名题在截止前加「暂定」。没分（还没交、或者学生看不到）
 *  时为空串。 */
const usageChip = computed(() => {
  const line = usage.value?.line
  const score = usage.value?.score
  if (!line || score == null) return ''
  const base = t('tasks.submissionHistory.usageChip', { score, points: line.points })
  return usage.value?.final === false ? t('tasks.submissionHistory.usageChipTentative', { base }) : base
})

// ── 手动项的上限 ──────────────────────────────────────────────────────────────
// 题目在这一版的阶段上有额度那一行时，评审里填的是**手动几项**的分（后端
// `usage_score.compose_review` 再加自动那部分）。上限是这一阶段手动项分值之和。

/** 这一版提交属于哪个阶段（老题/不分阶段的题是 null）。 */
const currentStage = computed(() => latestSubmission.value?.stage ?? null)

/** 额度那一行落在当前阶段上时给出手动项之和；否则 null（照老规矩 0–100）。 */
const manualCap = computed<number | null>(() => {
  const rubric = props.rubric ?? []
  const lines = rubric.filter((line) => (line.stage ?? null) === currentStage.value)
  if (!lines.some((line) => (line.kind ?? 'manual') !== 'manual')) return null
  return lines.reduce((sum, line) => sum + ((line.kind ?? 'manual') === 'manual' ? Number(line.points) || 0 : 0), 0)
})

const scoreMax = computed(() => manualCap.value ?? 100)

const totalMax = computed(() => {
  const cap = manualCap.value
  if (cap == null) return null
  const line = usage.value?.line
  return cap + (line ? Number(line.points) || 0 : 0)
})

/** 已判过的那一版评审里手动和自动各自的来源（老题的评审没有这一项）。 */
const autoScores = computed(() => latestSubmission.value?.review?.detail?.autoScores ?? null)

/** 「额度用量（自动）」那一格的分：已判的用当时算下的（`autoScores`），还没判的用此刻的
 *  估算（`usage.score`），都没有就画「—」。优先用已判那一版记下的 —— `points` 也一样，
 *  学生看不到评分要点时 `usage.line` 是没有的。 */
const autoLine = computed(() => {
  const saved = autoScores.value?.lines.find((line) => line.kind === 'usage')
  if (saved) return { score: saved.score, points: saved.points, tentative: !autoScores.value?.final }
  const line = usage.value?.line
  return { score: usage.value?.score ?? null, points: line?.points ?? 0, tentative: usage.value?.final === false }
})

const autoLineText = computed(() => {
  const { score, points, tentative } = autoLine.value
  if (score == null) return `— / ${points}`
  const text = `${score} / ${points}`
  return tentative ? t('tasks.submissionHistory.tentative', { score: text }) : text
})

/** 一个分怎么写：知道满分就写「n / 满分」，不知道就只写 n（学生看不到评分要点、题目又
 *  分了阶段时的样子 —— 上限算不出来，画一个假的分母比不画更糟）。 */
function ofMax(value: number | null): string {
  if (value == null) return '—'
  return totalMax.value == null ? `${value}` : `${value} / ${totalMax.value}`
}

/** 总分那一格：手动项填了就加额度那一行，没填就画「—」。 */
const totalPreview = computed(() => ofMax(score.value == null ? null : score.value + (autoLine.value.score ?? 0)))

/** 判过之后手动项那一格：上限算得出来就写「n / 上限」。 */
const savedManualText = computed(() => {
  const manual = autoScores.value?.manual ?? 0
  return manualCap.value == null ? `${manual}` : `${manual} / ${manualCap.value}`
})

const savedTotalText = computed(() => ofMax(latestSubmission.value?.review?.detail.score ?? null))

/** 额度那一行给分怎么变的：阈值题这一刻算下就记下，排名题要等截止定稿。 */
const usageModeHint = computed(() =>
  t(
    usage.value?.line?.mode === 'rank'
      ? 'tasks.submissionHistory.usageModeRank'
      : 'tasks.submissionHistory.usageModeThreshold'
  )
)

const calcSubmissionReviewColor = (review: TaskSubmissionReview) => {
  if (!review) {
    return 'text'
  }
  if (!review.reviewed) {
    return 'text'
  }
  if (review.detail.accepted) {
    return 'success'
  }
  return 'error'
}

const calcSubmissionReviewText = (review: TaskSubmissionReview) => {
  if (!review || !review.reviewed) {
    return t('tasks.submissionHistory.notReviewed')
  }
  return review.detail.accepted ? t('tasks.submissionHistory.accept') : t('tasks.submissionHistory.reject')
}

const { handleSubmit, defineField, isSubmitting, resetForm, errors } = useForm({
  initialValues: { accepted: true },
  // 上限跟着这一版的阶段走：有额度那一行时是这一阶段手动项之和，否则 100。写成
  // computed 而不是一次算好的对象 —— 切阶段（`props.stage` 变了）时上限要跟着变。
  validationSchema: computed(() =>
    toTypedSchema(
      z
        .object({
          accepted: z.boolean().default(true),
          score: z.number().optional(),
          comment: z.string().max(255).optional().default(''),
        })
        .superRefine((values, ctx) => {
          if (!values.accepted) return
          const { score } = values
          const max = scoreMax.value
          if (score === undefined || !Number.isInteger(score) || score < 0 || score > max) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['score'],
              // 0–100 的老题用原来那一句（一个字没变）；按额度计分的题说的是手动项的上限。
              message:
                manualCap.value == null
                  ? t('tasks.submissionHistory.scoreRequired')
                  : t('tasks.submissionHistory.scoreRequiredFor', { max }),
            })
          }
        })
    )
  ),
})

const [accepted, acceptedProps] = defineField('accepted', vuetifyConfig)
const [score] = defineField('score')
const [comment, commentProps] = defineField('comment', vuetifyConfig)
const scoreError = computed(() => errors.value.score)

/** 已判那一版评审里老师填的那部分分。题目按额度计分时评审存在库里的 `score` 是**总分**
 *  （手动 + 自动），改的时候要填回手动那一截，不然再存一次会把总分当手动分交上去。 */
const savedManual = computed(() => autoScores.value?.manual ?? latestSubmission.value?.review?.detail?.score)

const submitReview = handleSubmit(async (form) => {
  // The review row keeps a score either way; a rejection's is never shown, so it is 0.
  await saveReview({ accepted: form.accepted, score: form.accepted ? form.score ?? 0 : 0, comment: form.comment })
})

onMounted(refresh)

watch(() => [props.taskId, props.participantId, props.stage], refresh)

watch(latestSubmission, (newVal) => {
  if (props.reviewable && newVal.review) {
    if (newVal.review.reviewed) {
      accepted.value = newVal.review.detail.accepted
      score.value = savedManual.value
      comment.value = newVal.review.detail.comment
    } else {
      resetForm()
    }
  }
})

// A comment and a score are written for one verdict. Changing a saved review to
// the other verdict starts both over — otherwise a rejection's comment (and its
// stored 0) goes out with the pass. Changing back brings the saved ones back.
// What the reviewer has already typed for the new verdict is left alone.
watch(accepted, (now) => {
  const review = latestSubmission.value?.review
  if (!props.reviewable || !review?.reviewed) return
  const saved = review.detail
  const manual = savedManual.value
  if (now === saved.accepted) {
    if (!comment.value) comment.value = saved.comment
    if (score.value === undefined) score.value = manual
  } else {
    if (comment.value === saved.comment) comment.value = ''
    if (score.value === manual) score.value = undefined
  }
})

defineExpose({
  refresh,
})
</script>

<style scoped>
.gradient-card {
  background: linear-gradient(to right bottom, rgba(var(--v-theme-primary), 0.05), rgba(var(--v-theme-primary), 0.01));
  border: 1px solid rgba(var(--v-theme-primary), 0.1);
}

.border {
  border: 1px solid var(--line);
}

/* ── 额度那一块 ──────────────────────────────────────────────────────────────
   安静的一块：小标签、一个大数，不用卡片，也不上颜色 —— 只有按钮用主题色。 */
.tsh__usage {
  padding: 12px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.tsh__eyebrow {
  margin-bottom: 4px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.tsh__usage-top {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  align-items: baseline;
}

.tsh__big {
  color: var(--ink);
  font-size: 20px;
  line-height: 1.2;
}

.tsh__meta {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

/* 「按模型看」那一折：安静的一行小字，展开是一张两列的对齐表。 */
.tsh__more {
  margin-top: 8px;
}

.tsh__more summary {
  color: var(--muted);
  font-size: 13px;
  cursor: pointer;
}

.tsh__kv {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 2px 0;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

/* 手动项 / 额度用量 / 总分那三行账：两列对齐，标签在左、分在右。 */
.tsh__score {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 16px;
  padding: 8px 12px;
  background: var(--fill);
  border-radius: var(--radius-md);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.tsh__score .t-num {
  color: var(--text);
  text-align: end;
}

.tsh__total-label {
  color: var(--ink);
  font-weight: 600;
}
</style>
