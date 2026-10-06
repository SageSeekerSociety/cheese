/** 房间总览里的看板：和项目那块板同一套列、同一套短语，范围缩到一个房间。
 *
 * 「同一套」是这份用例要钉的东西。两处各叫各的名字，人就得在脑子里做一次翻译，
 * 而那次翻译迟早会错——这正是把状态收到后端算一次之后还要在前端共用一个模块的
 * 理由。
 *
 * 行是 props 进来的（取数在 `composables/usePanelOverview.ts`）：这一份只管画成
 * 什么样。谁在什么时候去拉、拉不到说什么，在那只组合式函数的用例里。
 */
import type { Component } from 'vue'
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import TaskProgress from './TaskProgress.vue'

import { setLocale } from '@/i18n'
import { BOARD_COLUMNS } from '@/lib/board'

// 断言按中文文案写：默认 locale 是 en，这里钉回 zh-CN。
beforeEach(() => setLocale('zh-CN'))

const Panel = TaskProgress as unknown as Component

function task(over: Partial<RoomTask> = {}): RoomTask {
  return {
    id: 'task-1',
    project_id: 'p1',
    room_id: 'room-1',
    title: '查一下分页接口',
    status: 'open',
    created_at: '2026-08-23T01:00:00Z',
    updated_at: '2026-08-23T01:00:00Z',
    presentation: { column: 'building', phrase: 'running' },
    ...over,
  }
}

let rows: RoomTask[] = [task()]

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  rows = [task()]
})

function mountClosed() {
  return render(Panel, { props: { rows }, global: { plugins: [vuetify] } })
}

// 这一段默认折着，下面这些用例看的是展开之后的清单，所以先点开——和人一样。
async function mount() {
  const view = mountClosed()
  await fireEvent.click(view.container.querySelector('.task-progress__head') as HTMLElement)
  return view
}

function titlesInColumn(container: Element, column: string): string[] {
  const head = container.querySelector(`[data-column="${column}"]`)
  const rows = head?.nextElementSibling?.querySelectorAll('.task-row__line1')
  return Array.from(rows ?? []).map((n) => (n.textContent ?? '').trim())
}

describe('列和短语跟项目那块板是同一套', () => {
  it('列的顺序和名字一字不差', async () => {
    rows = BOARD_COLUMNS.map((c, i) =>
      task({ id: `t${i}`, title: c.label, presentation: { column: c.key, phrase: 'running' } })
    )
    const { container } = await mount()
    expect(container.querySelectorAll('[data-column]').length).toBe(BOARD_COLUMNS.length)
    const names = Array.from(container.querySelectorAll('[data-column]')).map((n) => ({
      key: n.getAttribute('data-column'),
      label: n.querySelectorAll('span')[1]?.textContent?.trim(),
    }))
    expect(names).toEqual(BOARD_COLUMNS.map((c) => ({ key: c.key, label: c.label })))
  })

  it('行上写的是后端那句话，不是这一段自己想的词', async () => {
    rows = [task({ presentation: { column: 'needs_you', phrase: 'bounced' } })]
    const { findByText } = await mount()
    await findByText('已退回')
  })

  it('那句话按读者的语言画，切换语言时已经在屏上的也跟着变', async () => {
    // 后端给的是码：同一块板被说不同语言的人同时看，挑语言是读者屏幕的事。
    setLocale('en')
    rows = [task({ presentation: { column: 'needs_you', phrase: 'awaiting_review' } })]
    const { findByText } = await mount()
    await findByText('Awaiting review')
    setLocale('zh-CN')
    await findByText('待审阅')
  })

  it('这一版不认识的码照原样写出来，不留空白', async () => {
    rows = [task({ presentation: { column: 'building', phrase: 'warming_up' as RoomTask['presentation']['phrase'] } })]
    const { findByText } = await mount()
    await findByText('warming_up')
  })

  it('活按后端给的列分开', async () => {
    rows = [
      task({ id: 'a', title: '甲', presentation: { column: 'building', phrase: 'idle' } }),
      task({ id: 'b', title: '乙', presentation: { column: 'needs_you', phrase: 'awaiting_review' } }),
    ]
    const { container } = await mount()
    expect(titlesInColumn(container, 'needs_you')).toEqual(['第 2 件：乙'])
    expect(titlesInColumn(container, 'building')).toEqual(['第 1 件：甲'])
  })

  it('空的那一列不列：一排「0」只会把有东西的那几行往下推', async () => {
    const { container } = await mount()
    expect(container.querySelector('[data-column="building"]')).not.toBeNull()
    expect(container.querySelector('[data-column="needs_you"]')).toBeNull()
  })
})

describe('已完成收在最底下', () => {
  it('折起来时件数说得出来，展开后「已采纳」和「已关闭」分得清', async () => {
    rows = [
      task({ id: 'a', title: '甲', presentation: { column: 'done', phrase: 'accepted' } }),
      task({ id: 'b', title: '乙', presentation: { column: 'done', phrase: 'closed' } }),
    ]
    const { container, findByText, queryByText } = await mount()
    const fold = container.querySelector('.task-progress__group--fold')
    expect(fold, '还没渲染出折叠行').not.toBeNull()
    expect(fold!.querySelector('.task-progress__group-count')?.textContent?.trim()).toBe('2')
    expect(queryByText('已采纳')).toBeNull()

    await fireEvent.click(fold as HTMLElement)
    await findByText('已采纳')
    await findByText('已关闭')
  })
})

describe('标题旁边那个数', () => {
  it('说的是有几件在等人 —— 打开一个房间最该先看到的数', async () => {
    rows = [
      task({ id: 'a', presentation: { column: 'needs_you', phrase: 'awaiting_review' } }),
      task({ id: 'b', presentation: { column: 'needs_you', phrase: 'checks_failed' } }),
      task({ id: 'c', presentation: { column: 'building', phrase: 'running' } }),
    ]
    const { container } = await mount()
    expect(container.querySelector('.task-progress__tally')?.textContent?.replace(/\s+/g, '')).toBe('3件，2件待处理')
  })
})

describe('第 N 件的编号不跟着列走', () => {
  it('编号按派活的先后，排在哪一列都不变', async () => {
    // 编号是人在对话里指代一条活的方式（「第 3 件卡住了」）。跟着分列变的编号
    // 说的是别的活。
    rows = [
      task({ id: 'a', title: '老的', created_at: '2026-08-01T00:00:00Z' }),
      task({
        id: 'b',
        title: '新的',
        created_at: '2026-08-09T00:00:00Z',
        presentation: { column: 'needs_you', phrase: 'awaiting_review' },
      }),
    ]
    const { findByText } = await mount()
    await findByText(/第 1 件：老的/)
    await findByText(/第 2 件：新的/)
  })
})

describe('默认折着', () => {
  it('只剩一行摘要，清单要点开才有 —— 下面的文档不该被挤到只剩半截', async () => {
    const { container, findByText } = mountClosed()
    expect(container.querySelector('.task-progress__tally')?.textContent?.replace(/\s+/g, '')).toBe('1件')
    expect(container.querySelector('.task-row')).toBeNull()
    await fireEvent.click(container.querySelector('.task-progress__head') as HTMLElement)
    await findByText(/第 1 件：查一下分页接口/)
  })
})
