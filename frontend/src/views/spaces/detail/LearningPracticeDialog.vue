<template>
  <v-dialog
    :model-value="practice !== null"
    :max-width="DIALOG_WIDTH.lg"
    scrollable
    data-testid="learning-practice"
    @update:model-value="(value) => !value && emit('close')"
  >
    <v-card v-if="practice" rounded="lg">
      <v-card-title class="lp__title t-dialog-title">
        <span>
          {{ t('spaces.learningAnalysis.practice.title') }} ·
          <span data-user-content>{{ practice.knowledgePointName }}</span>
        </span>
        <span class="lp__chip">{{ t('spaces.learningAnalysis.practice.aiMade') }}</span>
      </v-card-title>
      <v-card-text class="lp__body">
        <p class="lp__muted">{{ t('spaces.learningAnalysis.practice.lede') }}</p>

        <section v-for="(item, index) in practice.questions" :key="index" class="lp__q" data-testid="practice-question">
          <h3 class="lp__stem">
            {{ index + 1 }}. <span data-user-content>{{ item.stem }}</span>
          </h3>
          <div
            class="lp__options"
            role="group"
            :aria-label="t('spaces.learningAnalysis.practice.question', { n: index + 1 })"
          >
            <button
              v-for="(option, choice) in item.options"
              :key="choice"
              type="button"
              class="lp__option"
              :class="optionClass(item, choice)"
              :disabled="item.chosen !== null || answering !== null || practice.done"
              :aria-pressed="item.chosen === choice"
              data-testid="practice-option"
              @click="emit('answer', index, choice)"
            >
              <span class="lp__letter">{{ LETTERS[choice] }}</span>
              <span data-user-content>{{ option }}</span>
            </button>
          </div>
          <p
            v-if="item.chosen !== null"
            class="lp__explain"
            :class="item.correct ? 'lp__explain--ok' : 'lp__explain--bad'"
            data-testid="practice-explanation"
          >
            <strong>{{
              item.correct ? t('spaces.learningAnalysis.practice.right') : t('spaces.learningAnalysis.practice.wrong')
            }}</strong>
            <span data-user-content>{{ item.explanation }}</span>
          </p>
        </section>

        <p v-if="result" class="lp__result" data-testid="practice-result">
          {{ resultLine }}
          <template v-if="result.nextReviewOn">
            <br />
            {{ t('spaces.learningAnalysis.practice.nextReview', { date: shortDate(result.nextReviewOn) }) }}
          </template>
        </p>
      </v-card-text>
      <v-card-actions class="px-4 pb-3">
        <template v-if="!practice.done">
          <BaseButton
            kind="ghost"
            :loading="regenerating"
            :disabled="completing"
            data-testid="practice-again"
            @click="emit('regenerate')"
          >
            {{ t('spaces.learningAnalysis.practice.again') }}
          </BaseButton>
          <v-spacer />
          <BaseButton
            kind="primary"
            :loading="completing"
            :disabled="!allAnswered || regenerating"
            data-testid="practice-done"
            @click="emit('complete')"
          >
            {{ t('spaces.learningAnalysis.practice.done') }}
          </BaseButton>
        </template>
        <template v-else>
          <v-spacer />
          <BaseButton kind="primary" data-testid="practice-close" @click="emit('close')">
            {{ t('spaces.learningAnalysis.practice.close') }}
          </BaseButton>
        </template>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
// 巩固练习的弹层：3 道选择题，答一道当场显示对错和讲解；都答完了「做完了」，把结果并进
// 这个知识点的判断。只摆、不取数：答题、交卷、换一组都交给容器（`MyLearningInsight.vue`）。
//
// 版式照可点原型 xueqing/demo.html 的 `vPractice()`：题干、四个选项、答后的讲解、底下
// 「做完了，更新我的学习洞察」与「换一组」。
import type { LearningLevel, LearningPracticeQuestion, LearningPracticeResult, LearningPracticeSet } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'

const props = withDefaults(
  defineProps<{
    /** 正在做的那一组；null 时弹层关着。 */
    practice: LearningPracticeSet | null
    /** 正在交的那道题。 */
    answering?: number | null
    completing?: boolean
    regenerating?: boolean
    /** 「做完了」之后的结果。 */
    result?: LearningPracticeResult | null
  }>(),
  { answering: null, completing: false, regenerating: false, result: null }
)
const emit = defineEmits<{
  answer: [question: number, choice: number]
  complete: []
  regenerate: []
  close: []
}>()
const { t, locale } = useI18n()

const LETTERS = ['A', 'B', 'C', 'D', 'E']

const allAnswered = computed(() => (props.practice?.questions ?? []).every((item) => item.chosen !== null))

function optionClass(item: LearningPracticeQuestion, choice: number): string {
  if (item.chosen === null) return ''
  if (choice === item.answer) return 'lp__option--right'
  if (choice === item.chosen) return 'lp__option--wrong'
  return ''
}

function levelLabel(level: LearningLevel): string {
  return t(`spaces.learningAnalysis.insight.levels.${level}`)
}

function shortDate(day: string): string {
  const [year, month, date] = day.split('-').map(Number)
  return new Intl.DateTimeFormat(locale.value, { month: 'numeric', day: 'numeric' }).format(
    new Date(year, month - 1, date)
  )
}

const resultLine = computed(() => {
  const result = props.result
  if (!result) return ''
  const score = t('spaces.learningAnalysis.practice.score', { correct: result.correct, total: result.total })
  if (!result.levelBefore || !result.levelAfter) return score
  if (result.levelBefore === result.levelAfter)
    return `${score} · ${t('spaces.learningAnalysis.practice.kept', { level: levelLabel(result.levelAfter) })}`
  return `${score} · ${t('spaces.learningAnalysis.practice.moved', {
    from: levelLabel(result.levelBefore),
    to: levelLabel(result.levelAfter),
  })}`
})
</script>

<style scoped>
.lp__title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.lp__chip {
  padding: 1px 8px;
  border-radius: var(--radius-pill);
  background: var(--fill-2);
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
}

.lp__body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.lp__muted {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.lp__q {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.lp__stem {
  margin: 0;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.lp__options {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.lp__option {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
  text-align: left;
  transition: border-color var(--dur-quick) var(--ease-standard);
}

.lp__option:hover:not(:disabled) {
  border-color: var(--muted);
}

.lp__option:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

.lp__option:disabled {
  cursor: default;
}

.lp__option--right {
  border-color: var(--ok);
  background: var(--ok-wash);
  color: var(--ok-ink);
}

.lp__option--wrong {
  border-color: var(--danger);
  background: var(--danger-wash);
  color: var(--danger-ink);
}

.lp__letter {
  flex: none;
  font-family: var(--font-mono);
  color: var(--muted);
}

.lp__explain {
  margin: 0;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--fill-2);
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.lp__explain strong {
  margin-right: 4px;
}

.lp__explain--ok strong {
  color: var(--ok-ink);
}

.lp__explain--bad strong {
  color: var(--danger-ink);
}

.lp__result {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  color: var(--ink);
}
</style>
