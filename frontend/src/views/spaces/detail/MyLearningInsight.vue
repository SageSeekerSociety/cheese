<template>
  <MyLearningInsightView
    :insight="insight"
    :loading="state === 'loading'"
    :failed="state === 'error'"
    :disputing="disputing"
    :realtime="realtime"
    :practice="practice.overview.value"
    :starting="practice.starting.value"
    :explanation="practice.explanation.value"
    :explaining="practice.explaining.value"
    @dispute="onDispute"
    @retry="load"
    @practice="onPractice"
    @open-practice="onOpen"
    @explain="onExplain"
  />
  <LearningPracticeDialog
    :practice="practice.current.value"
    :answering="practice.answering.value"
    :completing="practice.completing.value"
    :regenerating="practice.starting.value !== null"
    :result="practice.result.value"
    @answer="onAnswer"
    @complete="onComplete"
    @regenerate="onRegenerate"
    @close="practice.close()"
  />
</template>

<script setup lang="ts">
// 「我的学习洞察」的容器：读我自己在这块板的学情分析结果，接住「标记不准」，以及第 4 步的
// 巩固练习、复习提醒、讲解（`useMyLearningPractice`）。画面在 `MyLearningInsightView.vue` 与
// `LearningPracticeDialog.vue`（场景规则见 docs/manual/dev/scenes.md）。
//
// 只读本人那一份：接口不收「看谁」，这一页也不传。失败就说服务端那句话（「这条判断不存在，
// 可能已经更新了」「你今天已经用了 8 次」这类），比一句「操作失败」有用。
import type { LearningPracticeSource } from '@/types'

import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { useMyLearningPractice } from '@/composables/useMyLearningPractice'
import { useMyLearningInsight } from '@/composables/useSpaceLearningAnalysis'

import LearningPracticeDialog from './LearningPracticeDialog.vue'
import MyLearningInsightView from './MyLearningInsightView.vue'

import { axiosErrorMessage } from '@/api/axiosHttp'
import { toast } from '@/lib/toast'

const { t } = useI18n()
const route = useRoute()

const spaceId = () => Number(route.params.spaceId)
const { insight, state, realtime, disputing, load, dispute } = useMyLearningInsight(spaceId)
const practice = useMyLearningPractice(spaceId, load)

async function attempt(work: () => Promise<unknown>, fallback: string) {
  try {
    await work()
  } catch (failure) {
    toast.error(axiosErrorMessage(failure, t(fallback)))
  }
}

function onDispute(judgmentId: number) {
  return attempt(() => dispute(judgmentId), 'spaces.learningAnalysis.insight.disputeFailed')
}

function onPractice(pointId: number, source: Exclude<LearningPracticeSource, 'pushed'>) {
  return attempt(() => practice.start(pointId, { source }), 'spaces.learningAnalysis.practice.failed')
}

function onOpen(practiceId: number, pointId: number) {
  return attempt(() => practice.open(practiceId, pointId), 'spaces.learningAnalysis.practice.failed')
}

function onRegenerate() {
  const current = practice.current.value
  if (!current) return
  return attempt(
    () =>
      practice.start(current.knowledgePointId, {
        source: current.source === 'review' ? 'review' : 'self',
        fresh: true,
      }),
    'spaces.learningAnalysis.practice.failed'
  )
}

function onExplain(pointId: number) {
  return attempt(() => practice.explain(pointId), 'spaces.learningAnalysis.practice.explainFailed')
}

function onAnswer(question: number, choice: number) {
  return attempt(() => practice.answer(question, choice), 'spaces.learningAnalysis.practice.answerFailed')
}

function onComplete() {
  return attempt(() => practice.complete(), 'spaces.learningAnalysis.practice.completeFailed')
}
</script>
