// 「数据 › 学情分析」的取数：七个子页签各取各的（切过去才取，取过的留着），知识点详情、
// 匿名摘录、导出报告。画面在 `LearningAnalysisView.vue`，它只收这里给的值。
//
// 总览一进来就取：它的 `enabled` 决定这一格显示学情分析还是「去设置里开启」的提示。
import type { Ref } from 'vue'
import type {
  LearningClassBehavior,
  LearningClassExcerpts,
  LearningClassOverview,
  LearningClassPointDetail,
  LearningClassPoints,
  LearningClassQuestions,
  LearningClassTasks,
  LearningClassTrend,
  LearningClassWords,
  LearningExcerpt,
  LearningExportRequest,
  LearningPushResult,
} from '@/types/apiSpaces'
import type { LearningSub } from '../learningSubs'

import { computed, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useNavigation } from '@/composables/useNavigation'

import { buildSourceLink } from '../helpers'

import { axiosErrorMessage } from '@/api/axiosHttp'
import { SpacesApi } from '@/api/spaces'
import { announceDownload, announceDownloadFailure, saveBlob } from '@/lib/download'
import { toast } from '@/lib/toast'

type Pages = {
  overview: LearningClassOverview | null
  points: LearningClassPoints | null
  questions: LearningClassQuestions | null
  words: LearningClassWords | null
  tasks: LearningClassTasks | null
  behavior: LearningClassBehavior | null
  trend: LearningClassTrend | null
}

const EMPTY: Pages = {
  overview: null,
  points: null,
  questions: null,
  words: null,
  tasks: null,
  behavior: null,
  trend: null,
}

export function useLearningClass(spaceId: Readonly<Ref<number>>) {
  const { t } = useI18n()
  const nav = useNavigation()

  const sub = ref<LearningSub>('overview')
  const pages = shallowRef<Pages>({ ...EMPTY })
  const pointId = ref<number | null>(null)
  const pointDetail = shallowRef<LearningClassPointDetail | null>(null)
  const loading = ref(false)
  const failed = ref(false)
  const errorDetail = ref<string | null>(null)
  const ratios = ref<{ askedRatio?: number; stuckRatio?: number }>({})

  const enabled = computed(() => pages.value.overview?.enabled ?? null)

  const fetchPage = (which: LearningSub): Promise<Pages[LearningSub]> => {
    const id = spaceId.value
    switch (which) {
      case 'overview':
        return SpacesApi.getLearningClassOverview(id)
      case 'points':
        return SpacesApi.getLearningClassPoints(id, ratios.value)
      case 'questions':
        return SpacesApi.getLearningClassQuestions(id)
      case 'words':
        return SpacesApi.getLearningClassWords(id)
      case 'tasks':
        return SpacesApi.getLearningClassTasks(id)
      case 'behavior':
        return SpacesApi.getLearningClassBehavior(id)
      case 'trend':
        return SpacesApi.getLearningClassTrend(id)
    }
  }

  const run = async (task: () => Promise<void>) => {
    loading.value = true
    failed.value = false
    errorDetail.value = null
    try {
      await task()
    } catch (error) {
      failed.value = true
      errorDetail.value = error instanceof Error && error.message ? error.message : null
    } finally {
      loading.value = false
    }
  }

  const load = (which: LearningSub = sub.value, force = false) =>
    run(async () => {
      if (!spaceId.value || (!force && pages.value[which])) return
      const data = await fetchPage(which)
      pages.value = { ...pages.value, [which]: data }
    })

  const loadPoint = () =>
    run(async () => {
      if (pointId.value == null) return
      pointDetail.value = await SpacesApi.getLearningClassPoint(spaceId.value, pointId.value)
    })

  const retry = () => (pointId.value != null ? loadPoint() : load(sub.value, true))

  watch(
    spaceId,
    (value) => {
      pages.value = { ...EMPTY }
      pointId.value = null
      pointDetail.value = null
      sub.value = 'overview'
      if (value) load('overview').catch(() => undefined)
    },
    { immediate: true }
  )

  watch(sub, (value) => {
    load(value).catch(() => undefined)
  })

  const openPoint = (id: number) => {
    pointId.value = id
    pointDetail.value = null
    pushResult.value = null
    loadPoint().catch(() => undefined)
  }

  const closePoint = () => {
    pointId.value = null
    pointDetail.value = null
    pushResult.value = null
  }

  // --- 推送巩固题（第 4 步）：只回人数，推完重读详情（「上次推送」那一句跟着变）。 ----
  const pushing = ref(false)
  const pushResult = shallowRef<LearningPushResult | null>(null)

  const push = async (id: number) => {
    if (pushing.value) return
    pushing.value = true
    try {
      const result = await SpacesApi.pushLearningPractice(spaceId.value, id)
      if (pointId.value !== id) return
      pushResult.value = result
    } catch (failure) {
      toast.error(axiosErrorMessage(failure, t('spaces.analytics.learning.class.detail.pushFailed')))
    } finally {
      pushing.value = false
    }
  }

  const setRatios = (asked: number, stuck: number) => {
    ratios.value = { askedRatio: asked, stuckRatio: stuck }
    load('points', true).catch(() => undefined)
  }

  // --- 匿名对话摘录 -------------------------------------------------------
  const excerptsOpen = ref(false)
  const excerptsLoading = ref(false)
  const excerptsFailed = ref(false)
  const excerpts = shallowRef<LearningClassExcerpts | null>(null)

  const openExcerpts = async (group: { cluster: number } | { misconception: number }) => {
    excerptsOpen.value = true
    excerptsLoading.value = true
    excerptsFailed.value = false
    excerpts.value = null
    try {
      excerpts.value = await SpacesApi.getLearningClassExcerpts(spaceId.value, group)
    } catch {
      excerptsFailed.value = true
    } finally {
      excerptsLoading.value = false
    }
  }

  const openSource = (source: NonNullable<LearningExcerpt['source']>) => {
    nav?.navigate(buildSourceLink(source))
  }

  // --- 导出报告 -----------------------------------------------------------
  const exportOpen = ref(false)
  const exporting = ref(false)
  const exportPoint = computed(() =>
    pointId.value != null && pointDetail.value ? pointDetail.value.knowledgePoint : null
  )

  const openExport = () => {
    exportOpen.value = true
  }

  const doExport = async (request: LearningExportRequest) => {
    exporting.value = true
    try {
      const blob = await SpacesApi.exportLearningClassReport(spaceId.value, request)
      saveBlob(blob, `${t('spaces.analytics.learning.class.exportDialog.fileName')}.${request.format}`)
      announceDownload()
      exportOpen.value = false
    } catch {
      announceDownloadFailure(t('spaces.analytics.learning.class.exportDialog.failed'))
    } finally {
      exporting.value = false
    }
  }

  return {
    sub,
    pages,
    enabled,
    pointId,
    pointDetail,
    loading,
    failed,
    errorDetail,
    retry,
    openPoint,
    closePoint,
    pushing,
    pushResult,
    push,
    setRatios,
    excerptsOpen,
    excerptsLoading,
    excerptsFailed,
    excerpts,
    openExcerpts,
    openSource,
    exportOpen,
    exporting,
    exportPoint,
    openExport,
    doExport,
  }
}
