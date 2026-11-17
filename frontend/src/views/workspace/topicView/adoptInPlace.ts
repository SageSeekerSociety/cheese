// 主线上交付那几颗按钮：「查看改动」「预览」打开这件任务的页签停在那一种看法上；「采纳」
// 在主线上就地采纳这一版，人不用先去页签里。
//
// 采纳的是按下去那一刻这件任务等着审阅的那一张卡：先现读一次，拿它的版本去采纳（和页签
// 页头上那颗按钮同一个端点、同样带着看到的那一版）。读到的已经不在等审阅了（别人刚采纳
// 或退回），什么也不做：主线上的按钮跟着任务的状态就会收走。被拒的原因由报错那一层说。
import type { Ref } from 'vue'

import { ref } from 'vue'

import { acceptCard, getAcceptCards } from '@/api'
import { t } from '@/i18n'

export type DeliveryAct = { taskId: string; view: 'changes' | 'preview' | 'adopt' }

export function useAdoptInPlace(opts: {
  me: () => string
  agentName: () => string
  /** 打开这件任务的页签（手机上是整页）。 */
  openTask: (taskId: string) => Promise<unknown> | void
  /** 打开之后停在哪一种看法上。 */
  showView: (view: 'changes' | 'preview') => void
  reportError: (error: unknown, fallback: string) => void
  /** 采纳的结果不是采纳成了（合并冲突）：说一句。 */
  notice: (message: string) => void
}): { adopting: Ref<string | null>; onDelivery: (act: DeliveryAct) => Promise<void> } {
  const adopting = ref<string | null>(null)

  async function adopt(taskId: string) {
    if (adopting.value) return
    adopting.value = taskId
    try {
      const cards = (await getAcceptCards(taskId)).data
      const card = cards.find((c) => c.status === 'pending')
      if (!card) return
      const updated = await acceptCard(
        card.id,
        opts.me(),
        card.merge_state.head_sha,
        card.deliverable?.revision ?? null
      )
      if (updated.status === 'conflict') opts.notice(t('topic.accept.conflict', { agent: opts.agentName() }))
    } catch (e) {
      opts.reportError(e, t('topic.accept.acceptFailed'))
    } finally {
      adopting.value = null
    }
  }

  async function onDelivery(act: DeliveryAct) {
    if (act.view === 'adopt') return adopt(act.taskId)
    await opts.openTask(act.taskId)
    opts.showView(act.view)
  }

  return { adopting, onDelivery }
}
