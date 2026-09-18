/** 看板上的列名跟着语言走。
 *
 * 列名是这一页唯一走词表的字，所以扫的范围就画在那几块上：三个列头（名字 + 数）、
 * 顶上那行统计、折起来的「已完成」那一行。板面上其余的字（「暂无…的任务」「只看
 * 我的」「看板」这个标题、卡片上的字段）都是这一屏自己欠着的，不属于这一刀——扫
 * 整页会把它们一起算进来，红了也不知道是谁的错。
 *
 * 切语言那一条是这条例最要紧的地方：列名是从 `BOARD_COLUMNS` 上读出来的，当初那
 * 是模块加载时就冻结的一个常量（`label: COLUMN_LABEL[key]`），现在改成了取值器。
 * 常量也能过前两条（都是在挂载前就钉好语言的），只有这一条能证明它跟着语言走。
 */
import type { Component } from 'vue'
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectTasks = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual, listProjectTasks: (...a: unknown[]) => listProjectTasks(...a) }
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    topics: [{ id: 'room-1', title: 'room' }],
    members: [{ user_handle: 'ligan', role: 'member', name: 'Ligan' }],
    rootTopic: null,
    loadingTopics: false,
  }),
}))

import RunningWorkView from './RunningWorkView.vue'

import { setLocale } from '@/i18n'

const Board = RunningWorkView as unknown as Component

const CJK = /[㐀-䶿一-鿿豈-﫿]/

function task(over: Partial<RoomTask> = {}): RoomTask {
  return {
    id: 'task-1',
    project_id: 'p1',
    room_id: 'room-1',
    title: 'do a thing',
    status: 'open',
    owner_handle: 'ligan',
    created_at: '2026-08-23T01:00:00Z',
    updated_at: '2026-08-23T01:00:00Z',
    presentation: { column: 'building', display_status: '运行中' },
    ...over,
  } as RoomTask
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  // 板上有活的列和不空的「已完成」各一条：三列列头都在，折叠行也在。
  listProjectTasks.mockReset()
  listProjectTasks.mockResolvedValue({
    data: [task(), task({ id: 'task-2', presentation: { column: 'done', display_status: '已采纳' } })],
    total: 2,
  })
})

function mount() {
  return render(Board, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

const flat = (el: Element) => (el.textContent ?? '').replace(/\s+/g, '')
const texts = (container: Element, selector: string) =>
  Array.from(container.querySelectorAll(selector)).map((el) => (el.textContent ?? '').replace(/\s+/g, ' ').trim())

/** 三个列头：名字（单独一个 text 节点）和它旁边那个数。
 *  选择器带 `.board-col__head`：折起来的那一行里也有一个 `.board-col__count`。 */
const columnNames = (container: Element) => texts(container, '.board-col__name')
const columnCounts = (container: Element) => texts(container, '.board-col__head .board-col__count')
const columnHeads = (container: Element) =>
  Array.from(container.querySelectorAll('.board-col__head')).map((el) => flat(el))
const tally = (container: Element) => flat(container.querySelector('.board__head p') as Element)
const doneRow = (container: Element) => flat(container.querySelector('.board__done-head') as Element)

async function loaded(container: Element) {
  await waitFor(() => expect(columnHeads(container).length).toBe(3))
}

describe('讲中文', () => {
  it('列头、统计、折叠行念的都是原来那几句', async () => {
    setLocale('zh-CN')
    const { container } = mount()
    await loaded(container)

    expect(columnNames(container)).toEqual(['施工中', '交付中', '待处理'])
    expect(columnCounts(container)).toEqual(['1', '0', '0'])
    expect(columnHeads(container)).toEqual(['施工中1', '交付中0', '待处理0'])
    expect(tally(container)).toBe('施工中1·已完成1')
    expect(doneRow(container)).toBe('已完成1')
  })
})

describe('讲英文', () => {
  it('列头、统计、折叠行一个汉字都不剩', async () => {
    setLocale('en')
    const { container } = mount()
    await loaded(container)

    expect(columnNames(container)).toEqual(['Building', 'Delivering', 'Needs you'])
    expect(columnCounts(container)).toEqual(['1', '0', '0'])
    expect(columnHeads(container)).toEqual(['Building1', 'Delivering0', 'Needsyou0'])
    // 顶上那行统计拿的是同一批词，包括折起来的那一列。
    expect(tally(container)).toBe('Building1·Done1')
    expect(doneRow(container)).toBe('Done1')

    const scanned = [...columnHeads(container), tally(container), doneRow(container)].join(' ')
    expect(CJK.test(scanned), scanned).toBe(false)
  })
})

describe('切一次语言', () => {
  it('已经画出来的列名和统计当场跟着换', async () => {
    setLocale('zh-CN')
    const { container } = mount()
    await loaded(container)
    expect(columnNames(container)[0]).toBe('施工中')

    setLocale('en')
    await waitFor(() => expect(columnNames(container)[0]).toBe('Building'))
    expect(columnNames(container)).toEqual(['Building', 'Delivering', 'Needs you'])
    expect(tally(container)).toBe('Building1·Done1')
    expect(CJK.test(columnHeads(container).join(' ')), columnHeads(container).join(' ')).toBe(false)
  })
})
