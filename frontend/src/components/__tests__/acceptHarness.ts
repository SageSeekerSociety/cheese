// 采纳卡在任务页上的样子：页面取一份卡（provideAcceptCard），对话栏那一条和「改动」页
// 顶部那块读同一份。测试要按人看到的整页去点（横条上决定、顶部看详情和「更多操作」），
// 就得把两处一起挂上，这里就是那一页的最小形状。
import { defineComponent, h } from 'vue'

import { provideAcceptCard } from '@/composables/useAcceptCard'

import TopicAcceptCard from '../TopicAcceptCard.vue'
import AcceptReviewHead from '../work/AcceptReviewHead.vue'

export const AcceptPage = defineComponent({
  name: 'AcceptPage',
  props: {
    topicId: { type: String, required: true },
    topicStatus: { type: String, required: true },
    taskId: { type: String, default: null },
    reviewButton: { type: Boolean, default: false },
  },
  emits: ['phase', 'review', 'rejecting'],
  setup(props, { emit, expose }) {
    const card = provideAcceptCard(props)
    expose({ reload: card.reload })
    return () =>
      h('div', [
        h(AcceptReviewHead),
        h(TopicAcceptCard, {
          topicId: props.topicId,
          topicStatus: props.topicStatus,
          taskId: props.taskId,
          reviewButton: props.reviewButton,
          onPhase: (p: unknown) => emit('phase', p),
          onReview: () => emit('review'),
          onRejecting: (on: boolean) => emit('rejecting', on),
        }),
      ])
  },
})

/** happy-dom 没有 visualViewport 和 devicePixelRatio，而「更多操作」、确认框都是
 *  VOverlay：不补上，菜单根本挂不起来，测到的就成了「点了什么都没发生」。 */
export function stubOverlayGlobals(vi: { stubGlobal: (name: string, value: unknown) => unknown }) {
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('devicePixelRatio', 1)
}

async function settle() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 打开「改动」页顶部的「更多操作」，点其中写着某几个字的那一项。 */
export async function chooseMore(container: Element, label: string) {
  ;(container.querySelector('[aria-label="更多操作"]') as HTMLElement).click()
  await settle()
  const item = Array.from(document.querySelectorAll('.v-overlay .v-list-item')).find((n) =>
    n.textContent?.includes(label)
  ) as HTMLElement | undefined
  if (!item) throw new Error(`「更多操作」里没有「${label}」`)
  item.click()
  await settle()
}

/** 确认框（ConfirmDialog）里写着某几个字的那颗按钮。确认框挂在 body 上，不在组件里。 */
export function dialogButton(label: string): HTMLButtonElement | undefined {
  return Array.from(document.querySelectorAll<HTMLButtonElement>('.v-overlay .confirm-dialog button')).find(
    (b) => b.textContent?.trim() === label
  )
}

/** 「更多操作」里现在有哪几项（打开它，读出来；菜单留着开着）。 */
export async function moreItems(container: Element): Promise<string[]> {
  const trigger = container.querySelector('[aria-label="更多操作"]') as HTMLElement | null
  if (!trigger) return []
  trigger.click()
  await settle()
  const items = Array.from(document.querySelectorAll('.v-overlay--active .v-list-item')).map(
    (n) => n.textContent?.trim() ?? ''
  )
  return items
}
