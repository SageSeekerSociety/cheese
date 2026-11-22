// 「我的学习洞察」上的第 4 步：巩固练习、复习提醒、讲解（只有本人）。
//
// 一览（没做完的几组、复习提醒、今天还能要几次）随页面读一次；做题在一个弹层里：出题 / 打开
// 一组 → 一道一道答（答一道当场交回对错和讲解）→「做完了」并进判断。做完、换一组之后重读
// 一览，并告诉页面判断变了（`onChanged`），由页面去重读洞察。
//
// 要问模型的几步（出题、讲解）失败时把错误抛出去，由页面说服务端那句话（今天的次数用完了、
// 暂时用不了），比一句「操作失败」有用。切到别的空间时，迟到的回答不落到新空间上。
import type {
  LearningExplanation,
  LearningPracticeResult,
  LearningPracticeSet,
  LearningPracticeSource,
  MyLearningPractice,
} from '@/types'

import { type MaybeRefOrGetter, ref, toValue, watch } from 'vue'

import { SpacesApi } from '@/api/spaces'

export function useMyLearningPractice(spaceId: MaybeRefOrGetter<number | undefined>, onChanged: () => unknown) {
  const overview = ref<MyLearningPractice | null>(null)
  /** 弹层里正在做的那一组；没开弹层是 null。 */
  const current = ref<LearningPracticeSet | null>(null)
  const result = ref<LearningPracticeResult | null>(null)
  /** 正在出题的知识点（按钮转圈用）。 */
  const starting = ref<number | null>(null)
  /** 正在交的那道题。 */
  const answering = ref<number | null>(null)
  const completing = ref(false)
  const explanation = ref<LearningExplanation | null>(null)
  const explaining = ref<number | null>(null)

  function validId(): number | null {
    const id = toValue(spaceId)
    return id && Number.isFinite(id) && id > 0 ? id : null
  }

  async function load() {
    const id = validId()
    if (id === null) return
    try {
      const res = await SpacesApi.readMyLearningPractice(id)
      if (toValue(spaceId) === id) overview.value = res
    } catch {
      // 一览读不出来不挡住洞察那一页：卡片不摆就是了。
      if (toValue(spaceId) === id) overview.value = null
    }
  }

  /** 出一组（或交回这个知识点上没做完的那组）；`fresh` 是「换一组」。 */
  async function start(
    pointId: number,
    options: { source?: Exclude<LearningPracticeSource, 'pushed'>; fresh?: boolean } = {}
  ) {
    const id = validId()
    if (id === null || starting.value !== null) return
    starting.value = pointId
    try {
      const set = await SpacesApi.startLearningPractice(id, { knowledgePointId: pointId, ...options })
      if (toValue(spaceId) !== id) return
      current.value = set
      result.value = null
      void load()
    } finally {
      starting.value = null
    }
  }

  /** 打开没做完的一组：老师推来、还没出题的那组当场出。 */
  async function open(practiceId: number, pointId: number) {
    const id = validId()
    if (id === null) return
    const set = await SpacesApi.readLearningPractice(id, practiceId)
    if (toValue(spaceId) !== id) return
    if (!set.ready) {
      await start(pointId)
      return
    }
    current.value = set
    result.value = null
  }

  async function answer(question: number, choice: number) {
    const id = validId()
    const set = current.value
    if (id === null || set === null || answering.value !== null) return
    answering.value = question
    try {
      const got = await SpacesApi.answerLearningPractice(id, set.id, { question, choice })
      if (current.value?.id !== set.id) return
      current.value = {
        ...set,
        questions: set.questions.map((item, index) =>
          index === question
            ? {
                ...item,
                chosen: got.chosen,
                correct: got.correct,
                answer: got.answer,
                explanation: got.explanation,
              }
            : item
        ),
      }
    } finally {
      answering.value = null
    }
  }

  /** 「做完了」：交回结果，重读一览，让页面重读判断。 */
  async function complete() {
    const id = validId()
    const set = current.value
    if (id === null || set === null || completing.value) return
    completing.value = true
    try {
      const got = await SpacesApi.completeLearningPractice(id, set.id)
      if (current.value?.id !== set.id) return
      result.value = got
      current.value = {
        ...set,
        done: true,
        correct: got.correct,
        levelBefore: got.levelBefore,
        levelAfter: got.levelAfter,
      }
      void load()
      void onChanged()
    } finally {
      completing.value = false
    }
  }

  function close() {
    current.value = null
    result.value = null
  }

  /** 「先看一段讲解」。 */
  async function explain(pointId: number) {
    const id = validId()
    if (id === null || explaining.value !== null) return
    explaining.value = pointId
    try {
      const got = await SpacesApi.explainLearningPoint(id, { knowledgePointId: pointId })
      if (toValue(spaceId) === id) explanation.value = got
      void load()
    } finally {
      explaining.value = null
    }
  }

  watch(
    () => toValue(spaceId),
    () => {
      overview.value = null
      explanation.value = null
      close()
      void load()
    },
    { immediate: true }
  )

  return {
    overview,
    current,
    result,
    starting,
    answering,
    completing,
    explanation,
    explaining,
    load,
    start,
    open,
    answer,
    complete,
    close,
    explain,
  }
}
