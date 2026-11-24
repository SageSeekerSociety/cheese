/** 主线上处理一版交付的按钮：谁都能去看改动（有代码改动时）和预览（有东西可预览时）；只有这一版
 * 的审阅人能采纳和退回。 */
import type { TaskLine } from '@/lib/channelTasks'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import DeliveryActions from './DeliveryActions.vue'

import i18n, { setLocale, t } from '@/i18n'

const TASK: TaskLine = {
  id: 'task-15',
  title: '首页加报名表',
  owner: 'shirakawa',
  creator: 'shirakawa',
  state: {
    kind: 'review',
    stale: false,
    offline: false,
    status: 'in_review',
    icon: 'mdi-eye-outline',
    tone: 'warn',
    label: '待审阅',
    live: false,
    attention: null,
    line: '待你处理',
    parts: ['待你处理'],
    waits: '待你处理',
    tip: '',
  },
  mine: true,
  accepted: null,
  at: '',
  delivery: { reviewers: ['shirakawa'], code: true, preview: true },
}

function mount(task: TaskLine, viewer = 'shirakawa') {
  return render(DeliveryActions, {
    props: { task, viewer },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}
const button = (key: string) => screen.queryByRole('button', { name: t(`work.room.message.delivery.${key}`) })

beforeEach(() => setLocale('zh-CN'))

describe('主线上的交付', () => {
  it('审阅人能就地采纳，也能退回', async () => {
    const view = mount(TASK)
    await fireEvent.click(button('adopt')!)
    await fireEvent.click(button('return')!)
    expect(view.emitted().delivery).toEqual([
      [{ taskId: 'task-15', view: 'adopt' }],
      [{ taskId: 'task-15', view: 'return' }],
    ])
  })

  it('不是审阅人的人看得到改动和预览，不能采纳或退回', async () => {
    const view = mount(TASK, 'zhou')
    expect(button('adopt')).toBeNull()
    expect(button('return')).toBeNull()
    await fireEvent.click(button('changes')!)
    await fireEvent.click(button('preview')!)
    expect(view.emitted().delivery).toEqual([
      [{ taskId: 'task-15', view: 'changes' }],
      [{ taskId: 'task-15', view: 'preview' }],
    ])
  })

  it('审阅人有几位时每一位都能采纳', () => {
    mount({ ...TASK, delivery: { reviewers: ['linxiao', 'zhou'], code: true, preview: false } }, 'zhou')
    expect(button('adopt')).not.toBeNull()
  })

  it('没有代码改动不给「查看改动」，没有可预览的不给「预览」', () => {
    mount({ ...TASK, delivery: { reviewers: ['shirakawa'], code: false, preview: false } })
    expect(button('changes')).toBeNull()
    expect(button('preview')).toBeNull()
    expect(button('adopt')).not.toBeNull()
  })

  it('没在等审阅的任务没有按钮', () => {
    mount({ ...TASK, delivery: null })
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })
})
