/** 频道概览：进行中只列最近有动静的；综合最上面的项目目标只读，要改就整份打开。 */
import type { Component } from 'vue'
import type { RoomTask, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, within } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 正文的读法要整套块解析器；这里只关心它画了哪段字。
vi.mock('@/components/common/MarkdownView.vue', () => ({
  default: { name: 'MarkdownView', props: ['source'], template: '<div data-testid="md">{{ source }}</div>' },
}))

import ChannelOverview from './ChannelOverview.vue'

import i18n, { setLocale, t } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

const DAY = 86_400_000
const ago = (days: number) => new Date(Date.now() - days * DAY).toISOString()

function task(id: string, title: string, movedDaysAgo: number): RoomTask {
  return {
    id,
    project_id: 'p1',
    room_id: 'g1',
    title,
    title_source: 'human',
    status: 'open',
    owner_handle: 'alice',
    created_at: ago(90),
    last_message: { id: `m-${id}`, author: 'alice', content: '进展', created_at: ago(movedDaysAgo) },
    presentation: { column: 'doing', phrase: 'started' },
  } as unknown as RoomTask
}

const GENERAL = { id: 'g1', kind: 'root', title: '综合' } as unknown as Topic

function mount(props: Partial<Record<string, unknown>> = {}) {
  return render(ChannelOverview as unknown as Component, {
    props: {
      topic: GENERAL,
      general: true,
      overview: { id: 'doc1', projectId: 'p1', title: '' },
      overviewText: '',
      pins: [],
      tasks: [],
      canPin: true,
      memberNames: {},
      agentName: '芝士',
      saveDescription: async () => true,
      ...props,
    },
    global: { plugins: [vuetify, i18n] },
  })
}

describe('进行中的任务', () => {
  it('只列最近 7 天有动静的，其余从「全部任务」进', async () => {
    const ui = mount({
      tasks: [task('t1', '登录页改版', 1), task('t2', '八月的探针', 40), task('t3', '账单对账', 3)],
    })
    const listed = ui.getAllByTestId('channel-task').map((el) => el.textContent ?? '')
    expect(listed.some((text) => text.includes('登录页改版'))).toBe(true)
    expect(listed.some((text) => text.includes('账单对账'))).toBe(true)
    expect(listed.some((text) => text.includes('八月的探针'))).toBe(false)

    await fireEvent.click(ui.getByTestId('channel-running-more'))
    expect(ui.emitted()['open-all']).toHaveLength(1)
  })

  it('全都很久没动时一件也不列，数字跟着是 0，仍然够得着全部', () => {
    const ui = mount({ tasks: [task('t2', '八月的探针', 40)] })
    expect(ui.queryAllByTestId('channel-task')).toHaveLength(0)
    expect(ui.getByText(t('work.channel.overview.running', { count: 0 }))).toBeTruthy()
    expect(ui.getByTestId('channel-running-more')).toBeTruthy()
  })
})

describe('项目目标', () => {
  it('只读地显示，点编辑整份打开那一份文档', async () => {
    const ui = mount({ overviewText: '## 目标\n\n做一个课程助手。' })
    expect(ui.getByTestId('md').textContent).toContain('做一个课程助手')
    expect(ui.container.querySelector('[contenteditable="true"], textarea')).toBeNull()

    await fireEvent.click(ui.getByTestId('project-overview-edit'))
    expect(ui.emitted()['edit-overview']).toEqual([['doc1']])
  })

  it('还没写过时，给一个去写的入口', async () => {
    const ui = mount({ overviewText: '  ' })
    await fireEvent.click(ui.getByRole('button', { name: t('work.channel.overview.writeProject') }))
    expect(ui.emitted()['edit-overview']).toEqual([['doc1']])
  })
})

describe('等你处理', () => {
  beforeEach(() => localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' })))

  /** 一件等审阅的任务：卡请 `reviewers` 审（`needs_my_action` 只认得第一位）。 */
  function review(id: string, title: string, reviewers: string): RoomTask {
    return {
      ...task(id, title, 1),
      needs_my_action: reviewers.split('、')[0] === 'me',
      updated_at: ago(1),
      card: { id: `c-${id}`, status: 'pending', reviewer: reviewers, since: ago(1) },
      presentation: { column: 'needs_you', phrase: 'awaiting_review' },
    } as unknown as RoomTask
  }
  function asking(id: string, title: string, mine: boolean): RoomTask {
    return {
      ...task(id, title, 1),
      needs_my_action: mine,
      updated_at: ago(1),
      presentation: { column: 'needs_you', phrase: 'awaiting_answer' },
    } as unknown as RoomTask
  }

  it('列出等我审阅和等我回答的任务；点审阅的打开那一版，点提问的打开任务', async () => {
    const ui = mount({
      tasks: [
        review('r1', '报名表改短', 'me'),
        // One card asks two people: either of them is waited on.
        review('r3', '两人一起审的', 'bob、me'),
        asking('a1', '用哪本教材', true),
        review('r2', '等别人审阅的', 'bob'),
        asking('a2', '问别人的', false),
        task('t1', '登录页改版', 1),
      ],
    })
    const rows = ui.getAllByTestId('channel-waiting-task')
    expect(rows).toHaveLength(3)
    const text = ui.getByTestId('channel-waiting').textContent ?? ''
    expect(text).toContain('两人一起审的')
    expect(text).not.toContain('等别人审阅的')
    expect(text).not.toContain('问别人的')

    const section = within(ui.getByTestId('channel-waiting'))
    await fireEvent.click(section.getByText('报名表改短'))
    expect(ui.emitted()['open-delivery']).toEqual([['r1']])
    await fireEvent.click(section.getByText('用哪本教材'))
    expect(ui.emitted()['open-task']).toEqual([['a1']])
  })

  it('列在等你处理里的任务，不在进行中再列一遍，数字跟着少', () => {
    const ui = mount({
      tasks: [review('r1', '报名表改短', 'me'), review('r2', '等别人审阅的', 'bob'), task('t1', '登录页改版', 1)],
    })
    const running = ui.getByTestId('channel-running').textContent ?? ''
    expect(running).not.toContain('报名表改短')
    expect(running).toContain('登录页改版')
    expect(running).toContain(t('work.channel.overview.running', { count: 2 }))
  })

  it('进行中的全都在等你处理里时，进行中这一段不在', () => {
    const ui = mount({ tasks: [review('r1', '报名表改短', 'me'), asking('a1', '用哪本教材', true)] })
    expect(ui.getAllByTestId('channel-waiting-task')).toHaveLength(2)
    expect(ui.queryByTestId('channel-running')).toBeNull()
  })

  it('没有等我的，这一段整个不在', () => {
    const ui = mount({ tasks: [review('r2', '等别人审阅的', 'bob'), task('t1', '登录页改版', 1)] })
    expect(ui.queryByText(t('work.channel.overview.waiting'))).toBeNull()
  })
})
