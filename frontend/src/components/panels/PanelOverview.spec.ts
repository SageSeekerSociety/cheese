// @vitest-environment jsdom
/** 总览里点开一个任务，就去那个任务自己的页面：去哪儿是 `TopicView` 的事（它拿着
 * 地址），所以这一层必须把点开的是哪个任务原样透出去。
 *
 * TaskProgress 在这里换成只会 emit 的桩：要钉的是**这一层有没有把事件转出去**。
 */
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('./TaskProgress.vue', () => ({
  default: {
    name: 'TaskProgressStub',
    emits: ['open-card'],
    template: '<button type="button" class="task-stub" @click="$emit(\'open-card\', \'task-1\')">任务</button>',
  },
}))
// PanelDoc 那头拖着整个编辑器，jsdom 里连 import 都过不去。钉这一层的事，就别让它进场。
vi.mock('./PanelDoc.vue', () => ({ default: { name: 'PanelDocStub', template: '<div />' } }))

import PanelOverview from './PanelOverview.vue'

describe('总览里点开的任务', () => {
  it('把是哪个任务透出去', async () => {
    const { container, emitted } = render(PanelOverview, { props: { topic: null, activityTick: 0 } })
    await fireEvent.click(container.querySelector('.task-stub') as HTMLElement)
    expect(emitted()['open-card']).toEqual([['task-1']])
  })
})
