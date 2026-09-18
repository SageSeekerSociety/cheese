// @vitest-environment jsdom
/** 总览里点开一张卡，就在这一格往下钻一层（`?card=` 记着这一层）。卡上的按钮里
 * 「去验收」是唯一一个自己干不了活的：切到「改动」那一格是 `TopicView` 的事（它
 * 拿着地址），所以它必须原样透出去——以前这一层没接，那颗按钮点下去什么都不发生。
 *
 * PanelCard 在这里换成只会 emit 的桩：要钉的是**这一层有没有把事件转出去**，不是
 * 那张卡长什么样（那由 PanelCard.spec.ts 管）。
 */
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('./PanelCard.vue', () => ({
  default: {
    name: 'PanelCardStub',
    emits: ['back', 'review'],
    template: '<button type="button" class="card-stub" @click="$emit(\'review\')">去验收</button>',
  },
}))

// 另外两半这一格用不到，但它们是真组件：PanelDoc 那头拖着 monaco，jsdom 里连
// import 都过不去（`document.queryCommandSupported`）。钉这一层的事，就别让它们进场。
vi.mock('./PanelDoc.vue', () => ({ default: { name: 'PanelDocStub', template: '<div />' } }))
vi.mock('./TaskProgress.vue', () => ({ default: { name: 'TaskProgressStub', template: '<div />' } }))

import PanelOverview from './PanelOverview.vue'

describe('总览里的卡透出来的事件', () => {
  it('「去验收」原样透出去', async () => {
    const { container, emitted } = render(PanelOverview, {
      props: { topic: null, activityTick: 0, openCardId: 'task-1' },
    })
    const btn = container.querySelector('.card-stub') as HTMLElement
    expect(btn, '开了卡就该渲染出那张卡').toBeTruthy()
    await fireEvent.click(btn)
    expect(emitted().review).toBeTruthy()
  })
})
