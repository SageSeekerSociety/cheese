// 话题头部那颗 ⋯ 里的用量：这个话题用了多少、它所在的项目用了多少。
//
// 拆自 `components/TopicHeader.vue`：取数在这一层，组件只管画——组件不直接碰 API 层
// （`.claude/rules/architecture.md` 的组件边界）。用量原先是一个每 20 秒重取一次的
// 「资源」抽屉，后来退成 ⋯ 顶上的一段，取数一起搬到这里。
//
// 指针移到 ⋯ 上就开始取，点开菜单时多半已经到了；刚取过的（同一个话题、十秒以内）
// 不再取第二遍——悬停一次紧接着点开是常态。取的时候手里的旧数照样显示，只有一个数
// 都还没有时才画占位条（`TopicUsageSummary`），卡片从一出来就是最终尺寸。
// 没有定时器：20 秒轮询买来的新鲜度没人在看。
import type { MaybeRefOrGetter } from 'vue'
import type { UsageStats } from '@/cx_types'

import { ref, toValue, watch } from 'vue'

import { getProjectUsage, getTopicUsage } from '@/api'

/** 同一个话题十秒以内取过的这份不再取第二遍。 */
const FRESH_MS = 10_000

export interface TopicUsageScope {
  topicId: MaybeRefOrGetter<string | null | undefined>
  projectId: MaybeRefOrGetter<string | null | undefined>
}

/** 这一格手里的两份数：话题自己的，和它项目的。`load()` 由宿主在要用时叫。 */
export function useTopicUsage(scope: TopicUsageScope) {
  const loading = ref(false)
  const topic = ref<UsageStats | null>(null)
  const project = ref<UsageStats | null>(null)
  let loadedFor: string | null = null
  let loadedAt = 0

  /** 取此刻的两份用量。正取着、或者刚取过的不再取；取不回来就留着旧的（最坏是「暂无数据」）。 */
  async function load(): Promise<void> {
    const tid = toValue(scope.topicId)
    const pid = toValue(scope.projectId)
    if (!tid || !pid) return
    if (loading.value || (loadedFor === tid && Date.now() - loadedAt < FRESH_MS)) return
    loading.value = true
    try {
      const [tu, pu] = await Promise.all([getTopicUsage(tid), getProjectUsage(pid)])
      if (toValue(scope.topicId) !== tid) return
      topic.value = tu
      project.value = pu
      loadedFor = tid
      loadedAt = Date.now()
    } catch {
      // Best-effort; the popover just shows 暂无数据.
    } finally {
      loading.value = false
    }
  }

  // 换了话题，上一个话题的数不能挂在这一个上。
  watch(
    () => toValue(scope.topicId),
    () => {
      topic.value = null
      project.value = null
      loadedFor = null
    }
  )

  return { loading, topic, project, load }
}
