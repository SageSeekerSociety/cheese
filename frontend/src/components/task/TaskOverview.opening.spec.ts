/** 从讨论转出来的任务，文档还空着时先是 AI 队友在整理它：任务页说它整理到哪了；失败
 * 了，做这件任务的人能让它再试一次，谁都可以选择自己写。 */
import type { Component } from 'vue'
import type { RoomTask, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/components/work/PanelDocHost.vue', () => ({
  default: { name: 'PanelDocHost', template: '<div data-testid="doc" />' },
}))
vi.mock('@/components/task/TaskOutputs.vue', () => ({ default: { name: 'TaskOutputs', template: '<div />' } }))

import TaskOverview from './TaskOverview.vue'

import i18n, { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

const ROOM = { id: 'r1', project_id: 'p1', title: '前端', kind: 'topic', status: 'active' } as unknown as Topic

function mount(opening: RoomTask['opening'], canRetry = true) {
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
  return render(TaskOverview as unknown as Component, {
    props: {
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
    },
    global: { plugins: [vuetify, i18n] },
  })
}

describe('新任务的第一轮', () => {
  it('还在整理：说正在整理，不给重试', () => {
    const ui = mount('drafting')
    expect(ui.queryByRole('button', { name: '重试' })).toBeNull()
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
    expect(ui.getByTestId('doc')).toBeTruthy()
  })

  it('文档有内容以后（没有第一轮的状态）直接是文档', () => {
    const ui = mount(null)
    expect(ui.getByTestId('doc')).toBeTruthy()
  })
})
