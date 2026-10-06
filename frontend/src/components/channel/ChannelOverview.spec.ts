/** 频道概览：进行中只列最近有动静的；综合最上面的项目总览只读，要改就整份打开。 */
import type { Component } from 'vue'
import type { RoomTask, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

// 正文的读法要整套块解析器；这里只关心它画了哪段字。
vi.mock('@/components/common/MarkdownView.vue', () => ({
  default: { name: 'MarkdownView', props: ['source'], template: '<div data-testid="md">{{ source }}</div>' },
}))

import ChannelOverview from './ChannelOverview.vue'

import i18n, { setLocale } from '@/i18n'

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

  it('全都很久没动时一件也不列，仍然够得着全部', () => {
    const ui = mount({ tasks: [task('t2', '八月的探针', 40)] })
    expect(ui.queryAllByTestId('channel-task')).toHaveLength(0)
    expect(ui.getByTestId('channel-running-more')).toBeTruthy()
  })
})

describe('项目总览', () => {
  it('只读地显示，点「编辑」整份打开那一份文档', async () => {
    const ui = mount({ overviewText: '## 目标\n\n做一个课程助手。' })
    expect(ui.getByTestId('md').textContent).toContain('做一个课程助手')
    expect(ui.container.querySelector('[contenteditable="true"], textarea')).toBeNull()

    await fireEvent.click(ui.getByTestId('project-overview-edit'))
    expect(ui.emitted()['edit-overview']).toEqual([['doc1']])
  })

  it('还没写过时，给一个写一份的入口', async () => {
    const ui = mount({ overviewText: '  ' })
    await fireEvent.click(ui.getByRole('button', { name: '写一份' }))
    expect(ui.emitted()['edit-overview']).toEqual([['doc1']])
  })
})
