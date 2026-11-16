// 一块板的学情分析底座（§ learning analysis, step 1）：读开关 + 知识点清单 + 「我能不能
// 改」，以及改开关、清单 CRUD、AI 建议。
//
// 一个 composable 把这几件事收在一处，因为三个调用点问的是同一份状态：
//   - 设置页「学情分析」那一栏（管理入口，按 `canManage` 摆不摆）；
//   - 发题页 / 编辑页的「涉及的知识点」那一块（用 `enabled` 判显不显示，用
//     `knowledgePoints` 当候选）；
//   - 它们都要在改完清单后重新读一遍，所以重读也在这儿。
//
// **读是成员可读的**（`GET /spaces/{id}/learning-analysis`，`view`）：发题是成员的能力，
// 读不到清单就没法「从清单添加」。写那几条要 `edit_settings`，服务端拒了就抛出来。
//
// 与 `useSpaceMaterials` 同一个口径：**「读不出来」和「没有」分开**。一块刚建好、还没
// 读到 `enabled` 的板，不能当成「没开学情分析」——那会把那一块整个藏掉，而它可能正开
// 着。所以 `state` 分 `loading` / `ready` / `error`，用的人据此决定摆什么。
import type { SpaceKnowledgePoint, SpaceKnowledgeSuggestion, SpaceLearningNoticeState } from '@/types'

import { type MaybeRefOrGetter, ref, toValue, watch } from 'vue'

import { SpacesApi } from '@/api/spaces'

/** 这一份读到哪一步了。`error` 不能当成「没有」——见上。 */
export type LearningAnalysisState = 'loading' | 'ready' | 'error'

export function useSpaceLearningAnalysis(spaceId: MaybeRefOrGetter<number | undefined>) {
  /** 这块板开没开学情分析。`null` = 还没读到（别当成 false）。 */
  const enabled = ref<boolean | null>(null)
  const knowledgePoints = ref<SpaceKnowledgePoint[]>([])
  /** 我在这块板能不能改设置。`false` 也包含「还没读到」。 */
  const canManage = ref(false)
  const state = ref<LearningAnalysisState>('loading')

  async function load() {
    const id = toValue(spaceId)
    if (!id || !Number.isFinite(id) || id <= 0) {
      // 还没有一块板可问：维持「在读」。这会儿说「没开」是替一块还不认识的板下结论。
      state.value = 'loading'
      return
    }
    state.value = 'loading'
    try {
      const res = await SpacesApi.readLearningAnalysis(id)
      // 期间换了块板（或者页面被切走）就不要再落下来 —— 落下来的会是别人的。
      if (toValue(spaceId) !== id) return
      enabled.value = res.learningAnalysis.enabled
      knowledgePoints.value = res.knowledgePoints ?? []
      canManage.value = res.canManage
      state.value = 'ready'
    } catch {
      if (toValue(spaceId) !== id) return
      state.value = 'error'
    }
  }

  /** 改开关，成功就照服务端回来的更新本地那一格。 */
  async function setEnabled(next: boolean) {
    const id = toValue(spaceId)
    if (!id) return
    const res = await SpacesApi.updateLearningAnalysis(id, { enabled: next })
    enabled.value = res.learningAnalysis.enabled
  }

  /** 加一条知识点，加完接在清单末尾（顺序以服务端为准）。**交回新那一条**：「采用一条清单里
   *  还没有的建议」要拿它的编号挂到这道题上，调用点不该再去名字里猜。 */
  async function addKnowledgePoint(name: string): Promise<SpaceKnowledgePoint | null> {
    const id = toValue(spaceId)
    if (!id) return null
    const res = await SpacesApi.createKnowledgePoint(id, { name })
    knowledgePoints.value = [...knowledgePoints.value, res.knowledgePoint]
    return res.knowledgePoint
  }

  /** 改一条的名字。 */
  async function renameKnowledgePoint(pointId: number, name: string) {
    const id = toValue(spaceId)
    if (!id) return
    const res = await SpacesApi.updateKnowledgePoint(id, pointId, { name })
    knowledgePoints.value = knowledgePoints.value.map((p) => (p.id === pointId ? res.knowledgePoint : p))
  }

  /** 删一条（软删）。 */
  async function removeKnowledgePoint(pointId: number) {
    const id = toValue(spaceId)
    if (!id) return
    await SpacesApi.deleteKnowledgePoint(id, pointId)
    knowledgePoints.value = knowledgePoints.value.filter((p) => p.id !== pointId)
  }

  /** AI 读题面给出的建议。失败（网关不可用 / 超预算）抛出去，由调用点 toast。 */
  async function suggest(name: string, description: string): Promise<SpaceKnowledgeSuggestion[]> {
    const id = toValue(spaceId)
    if (!id) return []
    const res = await SpacesApi.suggestKnowledgePoints(id, { name, description })
    return res.suggestions ?? []
  }

  watch(
    () => toValue(spaceId),
    () => void load(),
    { immediate: true }
  )

  return {
    enabled,
    knowledgePoints,
    canManage,
    state,
    load,
    setEnabled,
    addKnowledgePoint,
    renameKnowledgePoint,
    removeKnowledgePoint,
    suggest,
  }
}

/** 「这块板开没开学情分析」的探针（发题页 / 编辑页只要这一个判断）。 */
export function useSpaceLearningEnabled(spaceId: MaybeRefOrGetter<number | undefined>) {
  const enabled = ref<boolean | null>(null)
  const state = ref<LearningAnalysisState>('loading')

  async function load() {
    const id = toValue(spaceId)
    if (!id || !Number.isFinite(id) || id <= 0) {
      state.value = 'loading'
      return
    }
    state.value = 'loading'
    try {
      const res = await SpacesApi.readLearningAnalysis(id)
      if (toValue(spaceId) !== id) return
      enabled.value = res.learningAnalysis.enabled
      state.value = 'ready'
    } catch {
      if (toValue(spaceId) !== id) return
      state.value = 'error'
    }
  }

  watch(
    () => toValue(spaceId),
    () => void load(),
    { immediate: true }
  )

  return { enabled, state, load }
}

/** 进这块板时那张告知卡要不要弹，以及记下「我已阅读并知悉」。 */
export function useSpaceLearningNotice(spaceId: MaybeRefOrGetter<number | undefined>) {
  const notice = ref<SpaceLearningNoticeState | null>(null)
  const state = ref<LearningAnalysisState>('loading')

  async function load() {
    const id = toValue(spaceId)
    if (!id || !Number.isFinite(id) || id <= 0) {
      state.value = 'loading'
      return
    }
    state.value = 'loading'
    // 换成别人的板（或者刚进这一页）时先别摆上一次读到的那份：弹出来的会是别的板该弹的卡。
    notice.value = null
    try {
      const res = await SpacesApi.readLearningNotice(id)
      if (toValue(spaceId) !== id) return
      notice.value = res
      state.value = 'ready'
    } catch {
      if (toValue(spaceId) !== id) return
      // 读不到就**不弹**（不打扰），但状态记下来，方便排查。
      state.value = 'error'
    }
  }

  async function acknowledge() {
    const id = toValue(spaceId)
    if (!id) return
    await SpacesApi.acknowledgeLearningNotice(id)
    // 记下已读：本地的这一份也跟着变，省得再读一次。
    notice.value = { enabled: notice.value?.enabled ?? true, needsAcknowledge: false, acknowledged: true }
  }

  watch(
    () => toValue(spaceId),
    () => void load(),
    { immediate: true }
  )

  return { notice, state, load, acknowledge }
}
