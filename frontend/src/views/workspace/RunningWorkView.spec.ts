/** 看板：一块板答的是「现在轮到谁」，不是「有哪些活、各是什么状态」。
 *
 * 这一份钉的是那个区别本身——列按「该谁动」分、短语一个字都不改地照抄后端、空列
 * 不消失、已完成不占板面，以及排序是全序（不然板会在两次刷新之间自己跳）。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Component } from 'vue'
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectTasks = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual, listProjectTasks: (...a: unknown[]) => listProjectTasks(...a) }
})

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push, replace: vi.fn() }), useRoute: () => ({ query: {} }) }))

vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    topics: [
      { id: 'room-1', title: '运维' },
      { id: 'room-2', title: '前端' },
    ],
    members: [{ user_handle: 'ligan', role: 'member', name: '李干' }],
  }),
}))

import RunningWorkView from './RunningWorkView.vue'

import { setLocale } from '@/i18n'

const Board = RunningWorkView as unknown as Component

function task(over: Partial<RoomTask> = {}): RoomTask {
  return {
    id: 'task-1',
    project_id: 'p1',
    room_id: 'room-1',
    title: '查一下分页接口',
    status: 'open',
    owner_handle: 'ligan',
    created_at: '2026-08-23T01:00:00Z',
    updated_at: '2026-08-23T01:00:00Z',
    presentation: { column: 'building', display_status: '运行中' },
    ...over,
  }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  // 列名走词表了，这一份断言的是中文那一边。happy-dom 的 navigator.language 是
  // en-US，不钉语言的话列头上写的是 Building。
  setLocale('zh-CN')
  push.mockReset()
  listProjectTasks.mockResolvedValue({ data: [task()], total: 1 })
})

function mount() {
  return render(Board, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

/** 一列的列头，形如「施工中 2」。空白由模板编译决定，不是这份用例要钉的东西。 */
function columnHead(container: Element, column: string): string {
  const head = container.querySelector(`[data-column="${column}"] .board-col__head`)
  const name = head?.querySelector('.board-col__name')?.textContent?.trim() ?? ''
  const count = head?.querySelector('.board-col__count')?.textContent?.trim() ?? ''
  return `${name} ${count}`
}

function titlesInColumn(container: Element, column: string): string[] {
  return Array.from(container.querySelectorAll(`[data-column="${column}"] .board-card__title`)).map((n) =>
    (n.textContent ?? '').trim()
  )
}

describe('列按「该谁动」分', () => {
  it('三件同样在等的活，按后端给的列各就各位', async () => {
    listProjectTasks.mockResolvedValue({
      data: [
        task({ id: 'a', title: '甲', presentation: { column: 'building', display_status: '运行中' } }),
        task({ id: 'b', title: '乙', presentation: { column: 'delivering', display_status: '等待检查' } }),
        task({ id: 'c', title: '丙', presentation: { column: 'needs_you', display_status: '等待验收' } }),
      ],
      total: 3,
    })
    const { container } = mount()
    await waitFor(() => expect(titlesInColumn(container, 'building')).toEqual(['甲']))
    expect(titlesInColumn(container, 'delivering')).toEqual(['乙'])
    expect(titlesInColumn(container, 'needs_you')).toEqual(['丙'])
  })

  it('同一件事在不同的列里说不同的话 —— 板不替后端翻译', async () => {
    // 快检红了：平台自己在修是「修复检查」，等人拍板是「检查未通过」。前端做第二
    // 张映射表的话，这个区别当场就没了。
    listProjectTasks.mockResolvedValue({
      data: [
        task({ id: 'a', title: '甲', presentation: { column: 'delivering', display_status: '修复检查' } }),
        task({ id: 'b', title: '乙', presentation: { column: 'needs_you', display_status: '检查未通过' } }),
      ],
      total: 2,
    })
    const { findByText } = mount()
    await findByText('修复检查')
    await findByText('检查未通过')
  })

  it('后端造出一个前端没见过的短语，照样原样显示', async () => {
    // 「失联」这类词是后端加的。前端有一张自己的表的话，新词只会变成一个空白。
    listProjectTasks.mockResolvedValue({
      data: [task({ presentation: { column: 'building', display_status: '失联' } })],
      total: 1,
    })
    const { findByText } = mount()
    await findByText('失联')
  })
})

describe('空列不消失', () => {
  it('一件活都没有的列仍然留着列头和 0', async () => {
    listProjectTasks.mockResolvedValue({
      data: [task({ presentation: { column: 'needs_you', display_status: '等待验收' } })],
      total: 1,
    })
    const { container } = mount()
    await waitFor(() => expect(columnHead(container, 'needs_you')).toBe('待处理 1'))
    // 整列消失会让板在两次刷新之间跳，而「待处理」在哪个位置本身就是信息。
    expect(columnHead(container, 'building')).toBe('施工中 0')
    expect(columnHead(container, 'delivering')).toBe('交付中 0')
  })
})

describe('已完成不占板面', () => {
  it('收进底部那条折叠行，件数写在按钮上', async () => {
    listProjectTasks.mockResolvedValue({
      data: [
        task({ id: 'a', title: '甲', presentation: { column: 'done', display_status: '已采纳' } }),
        task({ id: 'b', title: '乙', presentation: { column: 'done', display_status: '已收工' } }),
      ],
      total: 2,
    })
    const { container, findByText, queryByText } = mount()
    await findByText('已完成')
    // 折起来的时候板面上没有它们，但件数说得出来。
    expect(queryByText('甲')).toBeNull()
    expect(container.querySelector('.board__done .board-col__count')?.textContent).toBe('2')

    await fireEvent.click(container.querySelector('.board__done-head') as HTMLElement)
    await findByText('甲')
    // 「已采纳」和「已收工」的区别没有丢：同一列里是两个不同的短语。
    await findByText('已采纳')
    await findByText('已收工')
  })
})

describe('这一页原来的两个用处都还在', () => {
  it('房间满员标出来 —— 后面那些是真的在等，不是没人理', async () => {
    const running = { column: 'building', display_status: '运行中' } as const
    listProjectTasks.mockResolvedValue({
      data: [
        ...['a', 'b', 'c', 'd'].map((id) => task({ id, title: `跑-${id}`, presentation: { ...running } })),
        task({ id: 'e', title: '排队的', presentation: { column: 'building', display_status: '排队中' } }),
      ],
      total: 5,
    })
    const { findAllByText } = mount()
    expect((await findAllByText('房间满员')).length).toBe(5)
  })

  it('四条在跑分在两个房间，就没有一个房间是满的', async () => {
    const running = { column: 'building', display_status: '运行中' } as const
    listProjectTasks.mockResolvedValue({
      data: [
        ...['a', 'b'].map((id) => task({ id, room_id: 'room-1', presentation: { ...running } })),
        ...['c', 'd'].map((id) => task({ id, room_id: 'room-2', presentation: { ...running } })),
      ],
      total: 4,
    })
    const { container, queryByText } = mount()
    await waitFor(() => expect(container.querySelectorAll('.board-card').length).toBe(4))
    expect(queryByText('房间满员')).toBeNull()
  })

  it('顶上那行统计仍然在，用的是板自己的词', async () => {
    listProjectTasks.mockResolvedValue({
      data: [
        task({ id: 'a', presentation: { column: 'building', display_status: '运行中' } }),
        task({ id: 'b', presentation: { column: 'needs_you', display_status: '等待验收' } }),
        task({ id: 'c', presentation: { column: 'done', display_status: '已采纳' } }),
      ],
      total: 3,
    })
    const { container } = mount()
    await waitFor(() => {
      const head = container.querySelector('.board__head p')?.textContent?.replace(/\s+/g, '')
      expect(head).toBe('施工中1·待处理1·已完成1')
    })
  })

  it('跨房间的板上，每张卡说得出它是哪个房间的', async () => {
    // 截图里 AO 每张卡写的是分支名。我们的活没有自己的分支（多条活共用一棵树），
    // 写出来会是个假的事实；房间才是这块板上有用的那个坐标。
    listProjectTasks.mockResolvedValue({ data: [task({ room_id: 'room-2' })], total: 1 })
    const { findByText } = mount()
    await findByText('前端')
  })
})

describe('板不自己跳', () => {
  it('同一时刻更新的两件活有确定的先后', async () => {
    const same = '2026-08-23T05:00:00Z'
    listProjectTasks.mockResolvedValue({
      data: [task({ id: 'zzz', title: '后', updated_at: same }), task({ id: 'aaa', title: '先', updated_at: same })],
      total: 2,
    })
    const { container } = mount()
    await waitFor(() => expect(titlesInColumn(container, 'building')).toEqual(['先', '后']))
  })

  it('新动过的排前面', async () => {
    listProjectTasks.mockResolvedValue({
      data: [
        task({ id: 'a', title: '旧', updated_at: '2026-08-23T01:00:00Z' }),
        task({ id: 'b', title: '新', updated_at: '2026-08-23T09:00:00Z' }),
      ],
      total: 2,
    })
    const { container } = mount()
    await waitFor(() => expect(titlesInColumn(container, 'building')).toEqual(['新', '旧']))
  })
})

describe('卡片上的其余几行', () => {
  it('有 PR 才画那一行 —— 一条活骑一张卡，永远只有一个号', async () => {
    listProjectTasks.mockResolvedValue({
      data: [task({ card: { id: 'c1', status: 'pr_open', pr_number: 318 } })],
      total: 1,
    })
    const { findByText } = mount()
    await findByText('PR #318')
  })

  it('没有卡就没有那一行', async () => {
    const { container, queryByText } = mount()
    await waitFor(() => expect(container.querySelectorAll('.board-card').length).toBe(1))
    expect(queryByText(/^PR #/)).toBeNull()
  })

  it('没有主的时候说「暂无负责人」，不留一片空白', async () => {
    listProjectTasks.mockResolvedValue({ data: [task({ owner_handle: null })], total: 1 })
    const { findByText } = mount()
    await findByText('暂无负责人')
  })

  it('点一张卡就打开它所在的房间，并钻进这张卡', async () => {
    // 一件活不是地点：做它的分身住在房间的会话里。所以地址是房间的，卡在 query
    // 上——「你看一下这条活」因此还是一条能发出去的链接。
    const { container } = mount()
    await waitFor(() => expect(container.querySelector('.board-card')).not.toBeNull())
    await fireEvent.click(container.querySelector('.board-card') as HTMLElement)
    expect(push).toHaveBeenCalledWith({
      name: 'workspace-topic',
      params: { projectId: 'p1', topicId: 'room-1' },
      query: { tab: 'overview', card: 'task-1' },
    })
  })
})

describe('一件活都没有', () => {
  it('说「暂无派出去的任务」，而不是画三个空列了事', async () => {
    listProjectTasks.mockResolvedValue({ data: [], total: 0 })
    const { findByText } = mount()
    await findByText('暂无派出去的任务')
  })

  it('每一列自己说它空，「施工中」那一列还说得出下一步', async () => {
    // 这不是收尾的一句话：一个项目几百条活里同时活着的常常只有几条，三列全空是
    // 第一屏的常态，所以那几行字就是这一屏的主要内容。
    listProjectTasks.mockResolvedValue({ data: [], total: 0 })
    const { findByText } = mount()
    await findByText('暂无施工中的任务')
    await findByText('暂无交付中的任务')
    await findByText('暂无待处理的任务')
    await findByText('在房间里说明要做什么，芝士会把它拆成任务')
  })

  it('活全在「已完成」里的时候，板面照样说得出下一步', async () => {
    listProjectTasks.mockResolvedValue({
      data: [
        task({ id: 'a', title: '甲', presentation: { column: 'done', display_status: '已收工' } }),
        task({ id: 'b', title: '乙', presentation: { column: 'done', display_status: '已收工' } }),
      ],
      total: 2,
    })
    const { container, getByText } = mount()
    // 顶上那行仍然说得出这个项目交付过多少：板面空不等于什么都没发生过。
    await waitFor(() =>
      expect(container.querySelector('.board__head p')?.textContent?.replace(/\s+/g, '')).toBe('已完成2')
    )
    getByText('在房间里说明要做什么，芝士会把它拆成任务')
  })
})

// 板的高度必须在这一格上定死，列里那些 `min-height: 0` 才有意义。以前这里只有
// `flex: 1 1 auto`，而外面那层 `.project-shell` 只给了 `overflow: hidden`、不是
// flex 容器 —— 那句 flex 一路空转，板的高度等于内容高度，`.board-col__list` 的
// `overflow-y: auto` 于是永远等于自己的内容高、永远不滚：活一多，板的下半截就被
// 外壳裁掉，整页没有滚动条（马霄宇报「验收的地方显示不全」时一起查出来的同类）。
// 这是 CSS，jsdom 量不到布局，照 `PanelCard.spec.ts` 的老办法用源码断言钉住。
describe('板自己钉在视口高度上', () => {
  it('.board 有确定高度，而且是 border-box', () => {
    const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'RunningWorkView.vue'), 'utf8')
    const start = src.indexOf('\n.board {')
    expect(start).toBeGreaterThan(-1)
    const rule = src.slice(src.indexOf('{', start) + 1, src.indexOf('}', start)).replace(/\/\*[\s\S]*?\*\//g, '')
    expect(rule).toContain('height: 100%')
    // 这一格带内边距：content-box 会让它比 100% 再高 28px，底部照样被裁。
    expect(rule).toContain('box-sizing: border-box')
  })

  it('列里的清单仍然是唯一的滚动层', () => {
    const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'RunningWorkView.vue'), 'utf8')
    const start = src.indexOf('\n.board-col__list {')
    const rule = src.slice(src.indexOf('{', start) + 1, src.indexOf('}', start)).replace(/\/\*[\s\S]*?\*\//g, '')
    expect(rule).toContain('overflow-y: auto')
    expect(rule).toContain('min-height: 0')
  })
})
