<template>
  <!--
    小测。一条路由两种人看（`canTeach` 由服务端给，前端不自己再判一次权限）：

    - **成员**：这一周的小测，答完交卷 —— 客观题当场出分，简答显示「等管理员判」。
    - **管理员**：出题（卷面 + 答案键）、看全班交得怎么样、判简答的复核队列。

    答案键只有管理员那份载荷里有；成员那份连这一格都没有（截止前可以改答案重交，
    能拿到答案键就等于能抄）。
  -->
  <v-sheet flat rounded="lg" class="pa-4">
    <h1 class="text-h6 mb-3">{{ t('spaces.course.quiz.title') }}</h1>

    <div v-if="loading" class="text-center pa-6">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <template v-else-if="quiz">
      <v-sheet flat rounded="lg" class="section pa-4 mb-4">
        <div class="d-flex align-center flex-wrap ga-2 mb-1">
          <span class="text-body-1">{{ quiz.title }}</span>
          <v-chip v-if="unitLabel" size="small" variant="tonal">{{ unitLabel }}</v-chip>
          <v-btn v-if="canTeach" size="small" variant="text" icon="mdi-pencil-outline" @click="openQuizForm"></v-btn>
        </div>
        <div class="text-caption text-medium-emphasis">
          {{ dueLabel }}
        </div>
        <div class="text-caption text-medium-emphasis">
          {{ t('spaces.course.quiz.totalPoints', { points: maxScore }) }}
        </div>
      </v-sheet>

      <v-alert v-if="error" type="warning" variant="tonal" density="comfortable" class="mb-4">
        {{ error }}
      </v-alert>

      <!-- 成员：答完就看分 -->
      <v-sheet v-if="!canTeach" flat rounded="lg" class="section pa-4 mb-4">
        <div v-if="submitted" class="d-flex align-baseline flex-wrap ga-2 mb-3">
          <span class="text-subtitle-1">
            {{ t('spaces.course.quiz.myScore', { score: myScore, total: maxScore }) }}
          </span>
          <v-chip v-if="pendingReview" size="small" color="warning" variant="tonal">
            {{ t('spaces.course.quiz.pendingReview') }}
          </v-chip>
        </div>

        <div v-for="question in questions" :key="question.id" class="quiz-question py-3">
          <div class="d-flex align-baseline flex-wrap ga-2 mb-2">
            <span class="text-caption text-medium-emphasis">{{ question.position }}.</span>
            <span class="flex-grow-1">{{ question.prompt }}</span>
            <span class="text-caption text-medium-emphasis">
              {{ t('spaces.course.quiz.points', { points: question.points }) }}
            </span>
            <v-chip v-if="answerFor(question.id)" size="small" variant="tonal" :color="answerColor(question.id)">
              {{ answerLabel(question.id) }}
            </v-chip>
          </div>

          <v-radio-group
            v-if="question.kind === 'SINGLE_CHOICE'"
            :model-value="draft[question.id]"
            :disabled="closed || submitted"
            hide-details="auto"
            @update:model-value="draft[question.id] = $event"
          >
            <v-radio
              v-for="(option, index) in question.options"
              :key="index"
              :value="index"
              :label="option"
              density="comfortable"
            ></v-radio>
          </v-radio-group>

          <div v-else-if="question.kind === 'MULTIPLE_CHOICE'">
            <v-checkbox
              v-for="(option, index) in question.options"
              :key="index"
              :model-value="chosenIndices(question.id).includes(index)"
              :label="option"
              density="comfortable"
              hide-details="auto"
              :disabled="closed || submitted"
              @update:model-value="toggleChoice(question.id, index, $event)"
            ></v-checkbox>
          </div>

          <v-radio-group
            v-else-if="question.kind === 'TRUE_FALSE'"
            :model-value="draft[question.id]"
            :disabled="closed || submitted"
            hide-details="auto"
            @update:model-value="draft[question.id] = $event"
          >
            <v-radio :value="true" :label="t('spaces.course.quiz.true')" density="comfortable"></v-radio>
            <v-radio :value="false" :label="t('spaces.course.quiz.false')" density="comfortable"></v-radio>
          </v-radio-group>

          <v-text-field
            v-else-if="question.kind === 'FILL_BLANK'"
            autocomplete="off"
            :model-value="textAnswer(question.id)"
            :disabled="closed || submitted"
            variant="outlined"
            density="comfortable"
            hide-details="auto"
            @update:model-value="draft[question.id] = $event"
          ></v-text-field>

          <v-textarea
            v-else
            autocomplete="off"
            :model-value="textAnswer(question.id)"
            :disabled="closed || submitted"
            variant="outlined"
            density="comfortable"
            rows="3"
            hide-details="auto"
            @update:model-value="draft[question.id] = $event"
          ></v-textarea>

          <p v-if="answerFor(question.id)?.comment" class="text-caption text-medium-emphasis mt-2 mb-0">
            {{ answerFor(question.id)?.comment }}
          </p>
        </div>

        <v-btn
          color="primary"
          class="mt-4"
          :loading="saving"
          :disabled="closed || !questions.length"
          @click="submitAttempt"
        >
          {{ submitted ? t('spaces.course.quiz.resubmit') : t('spaces.course.quiz.submit') }}
        </v-btn>
        <p v-if="closed" class="text-medium-emphasis mt-2 mb-0">
          {{ t('spaces.course.quiz.closed') }}
        </p>
      </v-sheet>

      <!-- 管理员：卷面 + 复核队列 + 全班 -->
      <template v-else>
        <v-sheet flat rounded="lg" class="section pa-4 mb-4">
          <div class="d-flex align-center mb-3">
            <span class="text-subtitle-2 flex-grow-1">{{ t('spaces.course.quiz.paper') }}</span>
            <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-plus" @click="openQuestionForm(null)">
              {{ t('spaces.course.quiz.addQuestion') }}
            </v-btn>
          </div>

          <p v-if="!questions.length" class="text-medium-emphasis mb-0">
            {{ t('spaces.course.quiz.noQuestions') }}
          </p>

          <div v-for="question in questions" :key="question.id" class="quiz-question py-3">
            <div class="d-flex align-baseline flex-wrap ga-2">
              <span class="text-caption text-medium-emphasis">{{ question.position }}.</span>
              <span class="flex-grow-1">{{ question.prompt }}</span>
              <span class="text-caption text-medium-emphasis">
                {{ t('spaces.course.quiz.points', { points: question.points }) }}
              </span>
              <v-chip size="small" variant="text">{{ kindLabel(question.kind) }}</v-chip>
              <v-btn size="small" variant="text" icon="mdi-pencil-outline" @click="openQuestionForm(question)"></v-btn>
              <v-btn size="small" variant="text" icon="mdi-delete-outline" @click="removeQuestion(question)"></v-btn>
            </div>
            <ul v-if="question.options.length" class="text-body-2 mt-1 mb-0">
              <li v-for="(option, index) in question.options" :key="index">{{ option }}</li>
            </ul>
            <div class="text-caption text-medium-emphasis mt-1">
              {{ t('spaces.course.quiz.key') }}: {{ answerKeyLabel(question) }}
            </div>
          </div>
        </v-sheet>

        <v-sheet flat rounded="lg" class="section pa-4 mb-4">
          <div class="text-subtitle-2 mb-3">{{ t('spaces.course.quiz.reviewQueue') }}</div>
          <p v-if="!reviewQueue.length" class="text-medium-emphasis mb-0">
            {{ t('spaces.course.quiz.reviewEmpty') }}
          </p>
          <div v-for="item in reviewQueue" :key="item.answerId" class="review-item py-3">
            <div class="d-flex align-baseline flex-wrap ga-2 mb-1">
              <span class="text-body-2">{{ personLabel(item) }}</span>
              <span class="text-caption text-medium-emphasis">{{ item.prompt }}</span>
              <v-spacer></v-spacer>
              <span class="text-caption text-medium-emphasis">
                {{ t('spaces.course.quiz.points', { points: item.points }) }}
              </span>
            </div>
            <p class="text-body-2 mb-2">
              {{ String(item.response ?? '') || t('spaces.course.quiz.blank') }}
            </p>
            <p v-if="item.referenceAnswer" class="text-caption text-medium-emphasis mb-2">
              {{ t('spaces.course.quiz.key') }}: {{ String(item.referenceAnswer) }}
            </p>
            <v-btn size="small" color="primary" variant="tonal" @click="openGradeForm(item)">
              {{ t('spaces.course.quiz.grade') }}
            </v-btn>
          </div>
        </v-sheet>

        <v-sheet flat rounded="lg" class="section pa-4">
          <div class="text-subtitle-2 mb-3">{{ t('spaces.course.quiz.submissions') }}</div>
          <p v-if="!submissions.length" class="text-medium-emphasis mb-0">
            {{ t('spaces.course.quiz.nobodyYet') }}
          </p>
          <v-table v-else density="comfortable">
            <thead>
              <tr>
                <th>{{ t('spaces.course.quiz.colStudent') }}</th>
                <th>{{ t('spaces.course.quiz.colSubmitted') }}</th>
                <th>{{ t('spaces.course.quiz.colScore') }}</th>
                <th>{{ t('spaces.course.quiz.colState') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in submissions" :key="row.attemptId">
                <td>{{ personLabel(row) }}</td>
                <td>{{ dateLabel(row.submittedAt) }}</td>
                <td>{{ row.score }} / {{ row.maxScore }}</td>
                <td>
                  <v-chip size="small" variant="tonal" :color="row.gradedAt ? 'success' : 'warning'">
                    {{ row.gradedAt ? t('spaces.course.quiz.stateGraded') : t('spaces.course.quiz.stateWaiting') }}
                  </v-chip>
                </td>
              </tr>
            </tbody>
          </v-table>
        </v-sheet>
      </template>
    </template>

    <v-sheet v-else flat rounded="lg" class="section pa-6 text-center">
      <p class="text-medium-emphasis mb-2" data-test="no-quiz">
        {{ canTeach ? t('spaces.course.quiz.noQuiz') : t('spaces.course.quiz.noQuizStudent') }}
      </p>
      <v-btn v-if="canTeach" color="primary" variant="tonal" @click="openQuizForm">
        {{ t('spaces.course.quiz.createQuiz') }}
      </v-btn>
      <v-btn v-else-if="!targetUnitId()" variant="text" :to="{ name: 'SpacesCourseHome', params: { spaceId } }">
        {{ t('spaces.course.quiz.backToCourse') }}
      </v-btn>
    </v-sheet>

    <!-- 小测本身（标题 / 截止） -->
    <v-dialog v-model="quizDialogOpen" max-width="520">
      <v-card>
        <v-card-title>{{ t('spaces.course.quiz.form.quizTitle') }}</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="quizForm.title"
            autocomplete="off"
            :label="t('spaces.course.quiz.form.title')"
            variant="outlined"
            density="comfortable"
            class="mb-4"
          ></v-text-field>
          <v-text-field
            v-model="quizForm.dueAt"
            autocomplete="off"
            type="datetime-local"
            :label="t('spaces.course.quiz.form.dueAt')"
            :hint="t('spaces.course.quiz.form.dueAtHint')"
            persistent-hint
            variant="outlined"
            density="comfortable"
          ></v-text-field>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="quizDialogOpen = false">
            {{ t('spaces.course.quiz.form.cancel') }}
          </v-btn>
          <v-btn color="primary" :loading="saving" @click="saveQuiz">
            {{ t('spaces.course.quiz.form.save') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 一道题 -->
    <v-dialog v-model="questionDialogOpen" max-width="640">
      <v-card>
        <v-card-title>
          {{ editingQuestion ? t('spaces.course.quiz.form.editQuestion') : t('spaces.course.quiz.form.newQuestion') }}
        </v-card-title>
        <v-card-text>
          <v-select
            v-model="questionForm.kind"
            autocomplete="off"
            :items="kindItems"
            :label="t('spaces.course.quiz.form.kind')"
            variant="outlined"
            density="comfortable"
            class="mb-4"
          ></v-select>
          <v-textarea
            v-model="questionForm.prompt"
            autocomplete="off"
            :label="t('spaces.course.quiz.form.prompt')"
            variant="outlined"
            density="comfortable"
            rows="2"
            class="mb-4"
          ></v-textarea>
          <v-text-field
            v-model.number="questionForm.points"
            autocomplete="off"
            type="number"
            min="0"
            :label="t('spaces.course.quiz.form.points')"
            variant="outlined"
            density="comfortable"
            class="mb-4"
          ></v-text-field>

          <v-textarea
            v-if="isChoice"
            v-model="questionForm.optionsText"
            autocomplete="off"
            :label="t('spaces.course.quiz.form.options')"
            :hint="t('spaces.course.quiz.form.optionsHint')"
            persistent-hint
            variant="outlined"
            density="comfortable"
            rows="3"
            class="mb-4"
          ></v-textarea>

          <v-select
            v-if="questionForm.kind === 'SINGLE_CHOICE'"
            v-model.number="questionForm.singleAnswer"
            autocomplete="off"
            :items="optionItems"
            :label="t('spaces.course.quiz.form.key')"
            variant="outlined"
            density="comfortable"
            class="mb-4"
          ></v-select>

          <div v-else-if="questionForm.kind === 'MULTIPLE_CHOICE'" class="mb-4">
            <div class="text-caption text-medium-emphasis mb-1">
              {{ t('spaces.course.quiz.form.key') }}
            </div>
            <v-checkbox
              v-for="(option, index) in optionList"
              :key="index"
              :model-value="questionForm.multipleAnswer.includes(index)"
              :label="option"
              density="comfortable"
              hide-details="auto"
              @update:model-value="toggleKey(index, $event)"
            ></v-checkbox>
          </div>

          <v-radio-group
            v-else-if="questionForm.kind === 'TRUE_FALSE'"
            v-model="questionForm.boolAnswer"
            :label="t('spaces.course.quiz.form.key')"
            hide-details="auto"
            class="mb-4"
          >
            <v-radio :value="true" :label="t('spaces.course.quiz.true')" density="comfortable"></v-radio>
            <v-radio :value="false" :label="t('spaces.course.quiz.false')" density="comfortable"></v-radio>
          </v-radio-group>

          <v-textarea
            v-else-if="questionForm.kind === 'FILL_BLANK'"
            v-model="questionForm.acceptedText"
            autocomplete="off"
            :label="t('spaces.course.quiz.form.accepted')"
            :hint="t('spaces.course.quiz.form.acceptedHint')"
            persistent-hint
            variant="outlined"
            density="comfortable"
            rows="2"
          ></v-textarea>

          <v-textarea
            v-else
            v-model="questionForm.referenceText"
            autocomplete="off"
            :label="t('spaces.course.quiz.form.reference')"
            :hint="t('spaces.course.quiz.form.referenceHint')"
            persistent-hint
            variant="outlined"
            density="comfortable"
            rows="2"
          ></v-textarea>

          <v-alert v-if="questionError" type="warning" variant="tonal" density="comfortable" class="mt-4">
            {{ questionError }}
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="questionDialogOpen = false">
            {{ t('spaces.course.quiz.form.cancel') }}
          </v-btn>
          <v-btn color="primary" :loading="saving" @click="saveQuestion">
            {{ t('spaces.course.quiz.form.save') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 判一道简答 -->
    <v-dialog v-model="gradeDialogOpen" max-width="520">
      <v-card>
        <v-card-title>{{ t('spaces.course.quiz.form.gradeTitle') }}</v-card-title>
        <v-card-text>
          <p class="text-body-2 mb-3">{{ String(grading?.response ?? '') }}</p>
          <v-text-field
            v-model.number="gradeForm.points"
            autocomplete="off"
            type="number"
            min="0"
            :max="grading?.points ?? 0"
            :label="t('spaces.course.quiz.form.givePoints', { max: grading?.points ?? 0 })"
            variant="outlined"
            density="comfortable"
            class="mb-4"
          ></v-text-field>
          <v-textarea
            v-model="gradeForm.comment"
            autocomplete="off"
            :label="t('spaces.course.quiz.form.comment')"
            variant="outlined"
            density="comfortable"
            rows="2"
          ></v-textarea>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="gradeDialogOpen = false">
            {{ t('spaces.course.quiz.form.cancel') }}
          </v-btn>
          <v-btn color="primary" :loading="saving" @click="saveGrade">
            {{ t('spaces.course.quiz.form.save') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-sheet>
</template>

<script setup lang="ts">
import type {
  QuizAnswer,
  QuizQuestion,
  QuizQuestionKind,
  QuizReviewItem,
  QuizSubmission,
  TeachingQuizData,
  TeachingUnit,
} from '@/network/api/spaces/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { confirm } = useDialog()

const spaceId = Number(route.params.spaceId)

const data = ref<TeachingQuizData | null>(null)
const units = ref<TeachingUnit[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')

/** 成员答的草稿：题号 → 答案。形状随题型（服务端按同一套规则判）。 */
const draft = ref<Record<number, unknown>>({})

const quiz = computed(() => data.value?.quiz ?? null)
const canTeach = computed(() => data.value?.canTeach === true)
const questions = computed(() => data.value?.questions ?? [])
const submissions = computed<QuizSubmission[]>(() => data.value?.submissions ?? [])
const reviewQueue = computed<QuizReviewItem[]>(() => data.value?.reviewQueue ?? [])
const maxScore = computed(() => data.value?.maxScore ?? 0)
const myAnswers = computed<QuizAnswer[]>(() => data.value?.myAnswers ?? [])
const submitted = computed(() => Boolean(data.value?.myAttempt))
const myScore = computed(() => data.value?.myAttempt?.score ?? 0)
const pendingReview = computed(() => data.value?.myAttempt?.pendingReview === true)
const closed = computed(() => Boolean(quiz.value?.dueAt && quiz.value.dueAt <= Date.now()))

const unitInView = computed<TeachingUnit | null>(
  () => units.value.find((unit) => unit.id === quiz.value?.unitId) ?? null
)

const unitLabel = computed(() =>
  unitInView.value ? t('spaces.course.quiz.week', { week: unitInView.value.week }) : ''
)

const dueLabel = computed(() =>
  quiz.value?.dueAt ? t('spaces.course.quiz.due', { date: dateLabel(quiz.value.dueAt) }) : t('spaces.course.quiz.noDue')
)

// 题型名与后端 `quiz_models` 的集合一一对应。
const kindItems = computed(() => [
  { title: t('spaces.course.quiz.kind.SINGLE_CHOICE'), value: 'SINGLE_CHOICE' },
  { title: t('spaces.course.quiz.kind.MULTIPLE_CHOICE'), value: 'MULTIPLE_CHOICE' },
  { title: t('spaces.course.quiz.kind.TRUE_FALSE'), value: 'TRUE_FALSE' },
  { title: t('spaces.course.quiz.kind.FILL_BLANK'), value: 'FILL_BLANK' },
  { title: t('spaces.course.quiz.kind.SHORT_ANSWER'), value: 'SHORT_ANSWER' },
])

function kindLabel(kind: QuizQuestionKind): string {
  return t(`spaces.course.quiz.kind.${kind}`)
}

function dateLabel(ms: number): string {
  return new Date(ms).toLocaleString()
}

function personLabel(row: { user?: { nickname?: string; username: string }; userId: number }): string {
  return row.user?.nickname || row.user?.username || `#${row.userId}`
}

function answerFor(questionId: number): QuizAnswer | undefined {
  return myAnswers.value.find((answer) => answer.questionId === questionId)
}

function answerLabel(questionId: number): string {
  const answer = answerFor(questionId)
  if (!answer) return ''
  if (answer.needsReview) return t('spaces.course.quiz.awaitingTeacher')
  return t('spaces.course.quiz.got', { points: answer.awardedPoints ?? 0 })
}

function answerColor(questionId: number): string {
  const answer = answerFor(questionId)
  if (!answer || answer.needsReview) return 'warning'
  return (answer.awardedPoints ?? 0) > 0 ? 'success' : 'error'
}

function chosenIndices(questionId: number): number[] {
  const value = draft.value[questionId]
  return Array.isArray(value) ? (value as number[]) : []
}

function textAnswer(questionId: number): string {
  const value = draft.value[questionId]
  return typeof value === 'string' ? value : ''
}

function toggleChoice(questionId: number, index: number, checked: unknown) {
  const current = new Set(chosenIndices(questionId))
  if (checked) current.add(index)
  else current.delete(index)
  draft.value[questionId] = [...current].sort((a, b) => a - b)
}

// ── 载入 ────────────────────────────────────────────────────────────────

async function loadUnits() {
  try {
    const { data: unitsData } = await SpacesApi.listUnits(spaceId)
    units.value = unitsData.units
  } catch {
    units.value = []
  }
}

/** 地址带 `?unit=` 就回答那一周；没带（成员从别处点进来）就找**最近一周有小测的**。 */
function targetUnitId(): number | null {
  const fromQuery = Number(route.query.unit)
  if (Number.isFinite(fromQuery) && fromQuery > 0) return fromQuery
  const withQuiz = units.value.filter((unit) => unit.quizId)
  if (!withQuiz.length) return null
  return withQuiz.reduce((latest, unit) => (unit.week > latest.week ? unit : latest)).id
}

async function reload() {
  loading.value = true
  error.value = ''
  try {
    await loadUnits()
    const unitId = targetUnitId()
    if (unitId === null) {
      data.value = null
      loading.value = false
      return
    }
    const { data: payload } = await SpacesApi.getUnitQuiz(spaceId, unitId)
    data.value = payload
    draft.value = draftFromAnswers(payload.myAnswers ?? [])
  } catch {
    data.value = null
    error.value = t('spaces.course.quiz.loadFailed')
  }
  loading.value = false
}

function draftFromAnswers(answers: QuizAnswer[]): Record<number, unknown> {
  const out: Record<number, unknown> = {}
  for (const answer of answers) out[answer.questionId] = answer.response
  return out
}

// ── 成员：交卷 ──────────────────────────────────────────────────────────

async function submitAttempt() {
  if (!quiz.value) return
  saving.value = true
  error.value = ''
  try {
    const answers = questions.value.map((question) => ({
      questionId: question.id,
      response: draft.value[question.id] ?? null,
    }))
    const { data: payload } = await SpacesApi.submitQuizAttempt(spaceId, quiz.value.id, {
      answers,
    })
    data.value = payload
    draft.value = draftFromAnswers(payload.myAnswers ?? [])
  } catch {
    error.value = t('spaces.course.quiz.submitFailed')
  }
  saving.value = false
}

// ── 管理员：小测本身 ──────────────────────────────────────────────────────

const quizDialogOpen = ref(false)
const quizForm = ref({ title: '', dueAt: '' })

function openQuizForm() {
  quizForm.value = {
    title: quiz.value?.title ?? t('spaces.course.quiz.defaultTitle', { week: unitInView.value?.week ?? 1 }),
    dueAt: toLocalInput(quiz.value?.dueAt ?? null),
  }
  quizDialogOpen.value = true
}

async function saveQuiz() {
  const unitId = quiz.value?.unitId ?? targetUnitId()
  if (unitId === null) {
    error.value = t('spaces.course.quiz.pickWeekFirst')
    quizDialogOpen.value = false
    return
  }
  saving.value = true
  try {
    const dueAt = quizForm.value.dueAt ? toEpoch(quizForm.value.dueAt) : null
    if (quiz.value) {
      await SpacesApi.updateQuiz(spaceId, quiz.value.id, {
        title: quizForm.value.title,
        dueAt,
      })
    } else {
      await SpacesApi.createQuiz(spaceId, unitId, { title: quizForm.value.title, dueAt })
    }
    quizDialogOpen.value = false
    syncQuery(unitId)
    await reload()
  } catch {
    error.value = t('spaces.course.quiz.saveFailed')
  }
  saving.value = false
}

// ── 管理员：出题 ──────────────────────────────────────────────────────────

const questionDialogOpen = ref(false)
const editingQuestion = ref<QuizQuestion | null>(null)
const questionError = ref('')
const questionForm = ref({
  kind: 'SINGLE_CHOICE' as QuizQuestionKind,
  prompt: '',
  points: 2,
  optionsText: '',
  singleAnswer: 0,
  multipleAnswer: [] as number[],
  boolAnswer: true,
  acceptedText: '',
  referenceText: '',
})

const isChoice = computed(
  () => questionForm.value.kind === 'SINGLE_CHOICE' || questionForm.value.kind === 'MULTIPLE_CHOICE'
)
const optionList = computed(() =>
  questionForm.value.optionsText
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
)
const optionItems = computed(() => optionList.value.map((label, value) => ({ title: label, value })))

function toggleKey(index: number, checked: unknown) {
  const current = new Set(questionForm.value.multipleAnswer)
  if (checked) current.add(index)
  else current.delete(index)
  questionForm.value.multipleAnswer = [...current].sort((a, b) => a - b)
}

function openQuestionForm(question: QuizQuestion | null) {
  editingQuestion.value = question
  questionError.value = ''
  const key = question?.answer
  questionForm.value = {
    kind: (question?.kind ?? 'SINGLE_CHOICE') as QuizQuestionKind,
    prompt: question?.prompt ?? '',
    points: question?.points ?? 2,
    optionsText: (question?.options ?? []).join('\n'),
    singleAnswer: typeof key === 'number' ? key : 0,
    multipleAnswer: Array.isArray(key) ? (key as number[]) : [],
    boolAnswer: typeof key === 'boolean' ? key : true,
    acceptedText: Array.isArray(key) && question?.kind === 'FILL_BLANK' ? (key as string[]).join('\n') : '',
    referenceText: typeof key === 'string' ? key : '',
  }
  questionDialogOpen.value = true
}

/** 答案键的形状由题型决定 —— 服务端会再核一次，错了当场 400。 */
function answerPayload(): unknown {
  const form = questionForm.value
  if (form.kind === 'SINGLE_CHOICE') return form.singleAnswer
  if (form.kind === 'MULTIPLE_CHOICE') return form.multipleAnswer
  if (form.kind === 'TRUE_FALSE') return form.boolAnswer
  if (form.kind === 'FILL_BLANK') {
    return form.acceptedText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
  }
  return form.referenceText
}

async function saveQuestion() {
  if (!quiz.value) return
  saving.value = true
  questionError.value = ''
  try {
    const payload = {
      kind: questionForm.value.kind,
      prompt: questionForm.value.prompt,
      options: isChoice.value ? optionList.value : [],
      answer: answerPayload(),
      points: Number(questionForm.value.points) || 0,
    }
    if (editingQuestion.value) {
      await SpacesApi.updateQuizQuestion(spaceId, quiz.value.id, editingQuestion.value.id, payload)
    } else {
      await SpacesApi.addQuizQuestion(spaceId, quiz.value.id, payload)
    }
    questionDialogOpen.value = false
    await reload()
  } catch {
    questionError.value = t('spaces.course.quiz.questionFailed')
  }
  saving.value = false
}

async function removeQuestion(question: QuizQuestion) {
  if (!quiz.value) return
  const ok = await confirm(t('spaces.course.quiz.deleteQuestionConfirm'), {
    title: t('spaces.course.quiz.deleteQuestion'),
  }).wait()
  if (!ok) return
  await SpacesApi.deleteQuizQuestion(spaceId, quiz.value.id, question.id)
  await reload()
}

function answerKeyLabel(question: QuizQuestion): string {
  const key = question.answer
  if (question.kind === 'SINGLE_CHOICE' && typeof key === 'number') {
    return question.options[key] ?? String(key)
  }
  if (question.kind === 'MULTIPLE_CHOICE' && Array.isArray(key)) {
    return (key as number[]).map((index) => question.options[index] ?? String(index)).join(' / ')
  }
  if (question.kind === 'TRUE_FALSE') {
    return key ? t('spaces.course.quiz.true') : t('spaces.course.quiz.false')
  }
  if (Array.isArray(key)) return (key as string[]).join(' / ')
  return String(key ?? '')
}

// ── 管理员：判分 ──────────────────────────────────────────────────────────

const gradeDialogOpen = ref(false)
const grading = ref<QuizReviewItem | null>(null)
const gradeForm = ref({ points: 0, comment: '' })

function openGradeForm(item: QuizReviewItem) {
  grading.value = item
  gradeForm.value = { points: item.points, comment: '' }
  gradeDialogOpen.value = true
}

async function saveGrade() {
  if (!quiz.value || !grading.value) return
  saving.value = true
  try {
    await SpacesApi.gradeQuizAnswer(spaceId, quiz.value.id, grading.value.answerId, {
      points: Number(gradeForm.value.points) || 0,
      comment: gradeForm.value.comment,
    })
    gradeDialogOpen.value = false
    await reload()
  } catch {
    error.value = t('spaces.course.quiz.gradeFailed')
  }
  saving.value = false
}

// ── 小工具 ──────────────────────────────────────────────────────────────

/** 把小测钉在地址上：刷新之后还落回同一周。 */
function syncQuery(unitId: number) {
  if (Number(route.query.unit) === unitId) return
  void router.replace({ query: { ...route.query, unit: String(unitId) } })
}

function toLocalInput(ms: number | null): string {
  if (!ms) return ''
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function toEpoch(value: string): number | null {
  const ms = Date.parse(value)
  return Number.isFinite(ms) ? ms : null
}

onMounted(async () => {
  if (!Number.isFinite(spaceId) || spaceId <= 0) {
    loading.value = false
    return
  }
  await reload()
})
</script>

<style scoped lang="scss">
.section {
  border: 1px solid var(--line);
}

.quiz-question + .quiz-question,
.review-item + .review-item {
  border-top: 1px solid var(--line);
}
</style>
