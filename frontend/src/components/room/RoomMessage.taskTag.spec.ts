/** 关于某件任务的消息，头上挂那件任务的标签：点它打开那件任务。同一件任务最新的那一条
 * 标签上还写着状态词；早先的只写任务名，不让旧消息说一个已经过去的状态。
 */
import type { TaskLine } from '@/lib/channelTasks'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import RoomMessage from './RoomMessage.vue'

import i18n, { setLocale, t } from '@/i18n'

const TASK: TaskLine = {
  id: 'task-15',
  title: '首页加报名表',
  owner: 'shirakawa',
  creator: 'shirakawa',
  state: {
    kind: 'review',
    status: 'in_review',
    icon: 'mdi-eye-outline',
    tone: 'warn',
    label: '待审阅',
    live: false,
    attention: null,
    line: '等你',
    tip: '',
  },
  mine: true,
  accepted: null,
  at: '',
}

function mount(props: Record<string, unknown>) {
  return render(RoomMessage, {
    props: {
      block: { id: 'b1', kind: 'message', author: 'cheese-t1', created_at: '', content: '报名表做好了' },
      parent: null,
      parentName: null,
      runStart: true,
      mine: false,
      topicId: 't1',
      authorName: '芝士',
      avatar: null,
      isAgent: true,
      time: '10:00',
      refs: { mentionNames: {}, topicTitles: {} },
      viewer: 'shirakawa',
      ...props,
    } as never,
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('消息头上的任务标签', () => {
  it('点标签打开那件任务', async () => {
    const view = mount({ taskTag: TASK, taskTagFull: true })
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.taskTag.open', { title: TASK.title }) }))
    expect(view.emitted()['open-card']).toEqual([['task-15']])
  })

  it('最新的那一条写着状态和在等谁', () => {
    mount({ taskTag: TASK, taskTagFull: true })
    const tag = screen.getByRole('button', { name: t('work.room.taskTag.open', { title: TASK.title }) })
    expect(tag.textContent).toContain('待审阅')
    expect(tag.textContent).toContain('等你')
  })

  it('早先的那一条只写任务名', () => {
    mount({ taskTag: TASK, taskTagFull: false })
    const tag = screen.getByRole('button', { name: t('work.room.taskTag.open', { title: TASK.title }) })
    expect(tag.textContent).toContain(TASK.title)
    expect(tag.textContent).not.toContain('待审阅')
  })

  it('不关于任何任务的消息没有标签', () => {
    mount({})
    expect(screen.queryByRole('button', { name: t('work.room.taskTag.open', { title: TASK.title }) })).toBeNull()
  })
})
