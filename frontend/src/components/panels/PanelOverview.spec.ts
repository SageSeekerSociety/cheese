// @vitest-environment jsdom
/** 总览里点开一个任务，就去那个任务自己的页面：去哪儿是 `TopicView` 的事（它拿着
 * 地址），所以这一层必须把点开的是哪个任务原样透出去。
 *
 * TaskProgress 在这里换成只会 emit 的桩：要钉的是**这一层有没有把事件转出去**。
 */
import type { DocPeopleBundle } from '../../composables/useDocPeople'
import type { DocThreadsBundle } from '../../composables/useDocThreads'
import type { PanelDocBundle } from '../../composables/usePanelDoc'

import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('./TaskProgress.vue', () => ({
  default: {
    name: 'TaskProgressStub',
    emits: ['open-card'],
    template: '<button type="button" class="task-stub" @click="$emit(\'open-card\', \'task-1\')">任务</button>',
  },
}))
// 摆出来的东西那一格同理：它自己有 spec，这里只钉「点开一个」有没有被原样转出去。
vi.mock('./preview/RoomOutputs.vue', () => ({
  default: {
    name: 'RoomOutputsStub',
    emits: ['open'],
    template: '<button type="button" class="output-stub" @click="$emit(\'open\', \'output/报告.docx\')">产物</button>',
  },
}))
// PanelDoc 那头拖着整个编辑器，jsdom 里连 import 都过不去。钉这一层的事，就别让它进场。
vi.mock('./PanelDoc.vue', () => ({ default: { name: 'PanelDocStub', template: '<div />' } }))

import PanelOverview from './PanelOverview.vue'

// 文档那一格的取数：面板只是把它原样递给上面那个桩，所以三包空的就够。
const doc = {
  docPanel: {} as PanelDocBundle,
  docThreads: {} as DocThreadsBundle,
  docPeople: {} as DocPeopleBundle,
}

describe('总览里点开的任务', () => {
  it('把是哪个任务透出去', async () => {
    const { container, emitted } = render(PanelOverview, { props: { topic: null, activityTick: 0, ...doc } })
    await fireEvent.click(container.querySelector('.task-stub') as HTMLElement)
    expect(emitted()['open-card']).toEqual([['task-1']])
  })
})

describe('总览里点开一份摆出来的东西', () => {
  it('把点开的是哪个路径透出去', async () => {
    const { container, emitted } = render(PanelOverview, { props: { topic: null, activityTick: 0, ...doc } })
    await fireEvent.click(container.querySelector('.output-stub') as HTMLElement)
    expect(emitted()['open-output']).toEqual([['output/报告.docx']])
  })
})
