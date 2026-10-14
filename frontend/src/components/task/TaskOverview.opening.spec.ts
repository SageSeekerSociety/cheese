/** 从讨论转出来的任务，文档还空着时先是 AI 队友在整理它：任务页说它整理到哪了；失败
 * 了，做这件任务的人能让它再试一次，谁都可以选择自己写。 */
import type { Component } from 'vue'
import type { RoomTask, Topic } from '@/cx_types'
import type { DocReviewRequest } from '@/lib/docReview'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 这一格透出去的三样（闪一下、高亮某一轮、标出改过的那几处）记在这儿：它们是外面拿着
// 这一格的 ref 调的，到没到只有这里看得见。
const docHostCalls = vi.hoisted(() => [] as string[])

// `TaskOverview` 里 `PanelDocHost` 是 `defineAsyncComponent`（按需取）。`__esModule` 不
// 是给这个 spec 用的：Vue 拿到异步取回来的那一份之后，靠这个标记认出「组件在 `.default`
// 上」。少了它，被当成组件的是整个模块对象，`@vue/test-utils` 一读它的 `name` 就抛
// 「No "name" export is defined on the mock」。
vi.mock('@/components/work/PanelDocHost.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    __esModule: true,
    default: defineComponent({
      name: 'PanelDocHostStub',
      setup: (_props, { expose }) => {
        expose({
          pulse: () => docHostCalls.push('pulse'),
          highlightTurn: (turnId: string) => docHostCalls.push(`turn:${turnId}`),
          reviewEdits: () => docHostCalls.push('review'),
        })
        return () => h('div', { 'data-testid': 'doc' })
      },
    }),
  }
})
vi.mock('@/components/task/TaskOutputs.vue', () => ({ default: { name: 'TaskOutputs', template: '<div />' } }))

import TaskOverview from './TaskOverview.vue'

import i18n, { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

const ROOM = { id: 'r1', project_id: 'p1', title: '前端', kind: 'topic', status: 'active' } as unknown as Topic

function propsFor(opening: RoomTask['opening'], canRetry = true) {
  const task = {
    id: 't1',
    room_id: 'r1',
    title: '微信内预览打不开',
    status: 'open',
    owner_handle: 'alice',
    created_at: '2026-10-06T00:00:00Z',
    updated_at: '2026-10-06T00:00:00Z',
    presentation: { column: 'not_started', phrase: 'discussing' },
    opening,
  } as unknown as RoomTask
  return {
    room: ROOM,
    task,
    memberNames: {},
    activityTick: 0,
    topicList: [],
    agentName: '芝士',
    agentHandle: null,
    members: [],
    comparing: false,
    comparison: null,
    compareError: null,
    checklist: [],
    related: null,
    canRetry,
    retrying: false,
    retryError: null,
  }
}

function mount(opening: RoomTask['opening'], canRetry = true) {
  return render(TaskOverview as unknown as Component, {
    props: propsFor(opening, canRetry),
    global: { plugins: [vuetify, i18n] },
  })
}

interface OverviewApi {
  pulse: () => void
  highlightTurn: (turnId: string) => void
  reviewEdits: (request: DocReviewRequest) => void
}

/** 和 `TopicView` 一样，从外面拿着这一格 `defineExpose` 出来的那几样调它。 */
function mountWithRef(opening: RoomTask['opening']) {
  let overview: OverviewApi | null = null
  const Harness = defineComponent({
    setup: () => () =>
      h(TaskOverview as unknown as Component, {
        ...propsFor(opening),
        ref: (el: unknown) => {
          overview = el as OverviewApi | null
        },
      }),
  })
  const ui = render(Harness, { global: { plugins: [vuetify, i18n] } })
  return { ui, api: () => overview as unknown as OverviewApi }
}

describe('新任务的第一轮', () => {
  beforeEach(() => {
    docHostCalls.length = 0
  })

  it('还在整理：说正在整理，不给重试', () => {
    const ui = mount('drafting')
    expect(ui.queryByRole('button', { name: '重试' })).toBeNull()
    // 这一条不用等 `defineAsyncComponent`：`drafting` 走的是 `v-if="opening"` 那半边，
    // 编辑器那一格（`v-else`）不进渲染树，异步组件连要都不会被要。等在这儿只会超时。
    expect(ui.queryByTestId('doc')).toBeNull()
  })

  it('失败了：做这件任务的人能重试，重试就是把那条指令再交一次', async () => {
    const ui = mount('failed')
    await fireEvent.click(ui.getByRole('button', { name: '重试' }))
    expect(ui.emitted()['retry-opening']).toHaveLength(1)
  })

  it('不做这件任务的人没有重试', () => {
    const ui = mount('failed', false)
    expect(ui.queryByRole('button', { name: '重试' })).toBeNull()
  })

  it('选自己写：露出文档', async () => {
    const ui = mount('failed')
    await fireEvent.click(ui.getByRole('button', { name: '自己写' }))
    // 文档那一格是 `defineAsyncComponent`：同步找会在它解析之前就报没有。
    expect(await ui.findByTestId('doc')).toBeTruthy()
  })

  it('文档有内容以后（没有第一轮的状态）直接是文档', async () => {
    const ui = mount(null)
    expect(await ui.findByTestId('doc')).toBeTruthy()
  })

  // 「查看改动」是从聊天里递过来的，递的时候文档那一格可能还没长出来（整理中，或者失败
  // 那一屏挡在前面），而那一格自己还是按需取的，挂上还要等网络。两头都晚，那一次调用
  // 以前会被 `?.` 静默吞掉——不报错，高亮也不发生。
  it('递进来时文档那一格还没长出来：等它挂上也得标上改过的那几处', async () => {
    const { ui, api } = mountWithRef('failed')
    api().reviewEdits({ requester: '芝士', edits: [{ old: '30 天', new: '60 天' }] })
    expect(docHostCalls).toEqual([])

    await fireEvent.click(ui.getByRole('button', { name: '自己写' }))
    expect(await ui.findByTestId('doc')).toBeTruthy()

    await waitFor(() => expect(docHostCalls).toContain('review'))
  })
})
