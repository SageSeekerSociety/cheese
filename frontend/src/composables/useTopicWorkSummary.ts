// 这段对话此刻的工作摘要：芝士这一轮改了哪些文件、跑过没有。
//
// 拆自 `components/WorkPanel.vue`：取数这一层，组件只管画——组件不直接碰 API 层
// （`.claude/rules/architecture.md` 的组件边界）。它决定「改动」那一格的空满、页签上
// 那个角标，和面板开在哪一格，所以答案到没到、有没有新改动也归它记。
//
// 「新」的判据是「这些改动是不是你上次看过之后才有的」：芝士一轮干上几分钟，读者多半
// 正停在「文档」那一格，没有这个的话，一轮干出了东西这件事只有点进去比一比才知道。
import type { MaybeRefOrGetter } from 'vue'

import { computed, ref, toValue } from 'vue'

import { getTopicWorkSummary } from '@/api'

export interface TopicWorkSummaryScope {
  /** 读谁：任务是任务自己，频道是频道。 */
  topicId: MaybeRefOrGetter<string | null | undefined>
  projectId: MaybeRefOrGetter<string | null | undefined>
}

/** 这段对话的工作摘要，和它「有没有新改动」的那一份记账。 */
export function useTopicWorkSummary(scope: TopicWorkSummaryScope) {
  const summary = ref<{ changedFiles: string[]; hasRun: boolean }>({ changedFiles: [], hasRun: false })
  // 这个对话的摘要回来过没有。没回来之前「没有改动」还不是事实。
  const loaded = ref(false)
  // 改动清单上一次被看过的样子（文件名拼起来）：和它一样就是没有新东西。
  const seenKey = ref('')
  const key = computed(() => summary.value.changedFiles.join('\n'))
  const hasNew = computed(() => !!key.value && key.value !== seenKey.value)

  /** 把此刻的改动清单记成「看过了」。 */
  function markSeen() {
    seenKey.value = key.value
  }

  /**
   * 问一次。**回一个布尔：这次问到了没有。**
   *
   * 问不到不算一种状态——页签上显示的还是原来那些，但它照样算一个答案（`loaded` 跟着
   * 立起来），于是「开在哪一格」不会一直等一个可能永远不来的摘要。要不要顺手记成
   * 「看过了」（点进这一格、或者打开时就开着这一格）由调用方说。
   */
  async function poll(): Promise<boolean> {
    const tid = toValue(scope.topicId)
    const pid = toValue(scope.projectId)
    if (!tid || !pid) return false
    let next: { changed_files: string[]; has_run: boolean }
    try {
      next = await getTopicWorkSummary(pid, tid)
    } catch {
      loaded.value = true
      return false
    }
    summary.value = { changedFiles: next.changed_files, hasRun: next.has_run }
    loaded.value = true
    return true
  }

  return { summary, loaded, key, hasNew, markSeen, poll }
}
