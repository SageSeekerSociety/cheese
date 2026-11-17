// 主线上交付那几颗按钮：「查看改动」「预览」打开这件任务的页签停在那一种看法上；「采纳」
// 在主线上就地采纳这一版，人不用先去页签里；「退回」打开这件任务的页签，开出页签页头上
// 「退回」开的那一块写理由——退回只有那一条路。
//
// 采纳的是按下去那一刻这件任务等着审阅的那一张卡：先现读一次，拿它的版本去采纳，和页签
// 页头上那颗走同一个函数（`acceptCard/actions.ts` 的 `acceptAsShown`）。读到的已经不在等
// 审阅了（别人刚采纳或退回），什么也不做：主线上的按钮跟着任务的状态就会收走。被拒的原因
// （版本过时、合并态现在不让合）由报错那一层说。
import type { Ref } from 'vue'
import type { DeliveryAct } from '@/lib/channelTasks'
import type { useWorkspaceStore } from '@/stores/workspace'

import { ref } from 'vue'

import { acceptAsShown } from '@/composables/acceptCard/actions'

import { getAcceptCards } from '@/api'
import { t } from '@/i18n'

export function useAdoptInPlace(opts: {
  me: () => string
  store: ReturnType<typeof useWorkspaceStore>
  /** 采纳之后要重读的那个频道（任务的状态、主线上的按钮跟着它变）。 */
  topicId: () => string
  /** 打开这件任务的页签（手机上是整页）。 */
  openTask: (taskId: string) => Promise<unknown> | void
  /** 打开之后停在哪一种看法上。 */
  showView: (taskId: string, view: 'changes' | 'preview') => void
  /** 在打开的页签里开出写退回理由的那一块。 */
  askReturn: (taskId: string) => void
}): { adopting: Ref<string | null>; onDelivery: (act: DeliveryAct) => Promise<void> } {
  const adopting = ref<string | null>(null)

  async function adopt(taskId: string) {
    if (adopting.value) return
    adopting.value = taskId
    try {
      const card = (await getAcceptCards(taskId)).data.find((c) => c.status === 'pending')
      if (!card) return
      await acceptAsShown(card, opts.me(), opts.store)
      await opts.store.refreshTopicRow(opts.topicId())
    } catch (e) {
      opts.store.reportError(e, t('topic.accept.acceptFailed'))
    } finally {
      adopting.value = null
    }
  }

  async function onDelivery(act: DeliveryAct) {
    if (act.view === 'adopt') return adopt(act.taskId)
    await opts.openTask(act.taskId)
    if (act.view === 'return') {
      // 写理由时看着改动：退回说的就是这一版哪里不对。
      opts.showView(act.taskId, 'changes')
      opts.askReturn(act.taskId)
      return
    }
    opts.showView(act.taskId, act.view)
  }

  return { adopting, onDelivery }
}
