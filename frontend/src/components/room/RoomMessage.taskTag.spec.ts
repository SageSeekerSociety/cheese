/** 关于某件任务的消息，头上挂那件任务的标签：点它打开那件任务。同一件任务最新的那一条
 * 标签上还写着状态词；早先的只写任务名，不让旧消息说一个已经过去的状态。
 */
import type { TaskLine } from '@/lib/channelTasks'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, within } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import RoomMessage from './RoomMessage.vue'

import i18n, { setLocale, t } from '@/i18n'

const TASK: TaskLine = {
  id: 'task-15',
  title: '首页加报名表',
  owner: 'shirakawa',
  creator: 'shirakawa',
  teammate: 'cheese-t1',
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
    line: '等你',
    waits: '等你',
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

  it('别的 AI 队友做的任务，标签上有做它的那位的头像', () => {
    mount({
      taskTag: { ...TASK, teammate: 'cheese-yan' },
      nameOfHandle: (handle: string) => (handle === 'cheese-yan' ? '小研' : '芝士'),
    })
    const tag = screen.getByRole('button', { name: t('work.room.taskTag.open', { title: TASK.title }) })
    expect(within(tag).getByRole('img', { name: '小研' })).toBeTruthy()
  })

  it('说话的就是做它的那位时，标签上不画头像', () => {
    mount({ taskTag: TASK, nameOfHandle: () => '芝士' })
    const tag = screen.getByRole('button', { name: t('work.room.taskTag.open', { title: TASK.title }) })
    expect(within(tag).queryByRole('img', { name: '芝士' })).toBeNull()
  })

  it('不关于任何任务的消息没有标签', () => {
    mount({})
    expect(screen.queryByRole('button', { name: t('work.room.taskTag.open', { title: TASK.title }) })).toBeNull()
  })
})

/** 交了一版在等人审阅：关于这件任务的最新一条话下面，谁都能去看改动和预览，只有这一版的审阅人
 * 能就地采纳。 */
describe('主线上的交付', () => {
  const DELIVERED: TaskLine = { ...TASK, delivery: { reviewers: ['shirakawa'], code: true } }
  const group = () =>
    screen.queryByRole('group', { name: t('work.room.message.delivery.label', { title: TASK.title }) })

  it('审阅人点「采纳」就地采纳这一版', async () => {
    const view = mount({ taskTag: DELIVERED, taskTagFull: true })
    await fireEvent.click(within(group()!).getByRole('button', { name: t('work.room.message.delivery.adopt') }))
    expect(view.emitted().delivery).toEqual([[{ taskId: 'task-15', view: 'adopt' }]])
  })

  it('不是审阅人的人看得到改动和预览，没有采纳', async () => {
    const view = mount({ taskTag: DELIVERED, taskTagFull: true, viewer: 'zhou' })
    const buttons = within(group()!)
    expect(buttons.queryByRole('button', { name: t('work.room.message.delivery.adopt') })).toBeNull()
    await fireEvent.click(buttons.getByRole('button', { name: t('work.room.message.delivery.changes') }))
    await fireEvent.click(buttons.getByRole('button', { name: t('work.room.message.delivery.preview') }))
    expect(view.emitted().delivery).toEqual([
      [{ taskId: 'task-15', view: 'changes' }],
      [{ taskId: 'task-15', view: 'preview' }],
    ])
  })

  it('没有代码改动的任务只给预览', () => {
    mount({ taskTag: { ...DELIVERED, delivery: { reviewers: ['shirakawa'], code: false } }, taskTagFull: true })
    expect(within(group()!).queryByRole('button', { name: t('work.room.message.delivery.changes') })).toBeNull()
  })

  it('同一件任务早先的那几条话下面没有按钮', () => {
    mount({ taskTag: DELIVERED, taskTagFull: false })
    expect(group()).toBeNull()
  })

  it('没在等审阅的任务没有按钮', () => {
    mount({ taskTag: { ...TASK, delivery: null }, taskTagFull: true })
    expect(group()).toBeNull()
  })
})
