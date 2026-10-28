// 全部任务：下一步在人手上的排最前；等你的写成「待你…」；十四天没动静的收进「已停滞」，
// 点开才列出来；选了频道就只列那个频道的任务。
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, within } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import ProjectTasksView from './ProjectTasksView.vue'

import { setLocale, t } from '@/i18n'

Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })

let n = 0
function task(over: Partial<RoomTask>): RoomTask {
  n += 1
  return {
    id: `t${n}`,
    project_id: 'p',
    room_id: 'front',
    title: `任务 ${n}`,
    status: 'open',
    owner_handle: 'bob',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    last_activity_at: '2026-10-01T00:00:00Z',
    presentation: { column: 'building', phrase: 'started' },
    ...over,
  }
}

const channels = [
  { id: 'front', title: '前端', glyph: 'mdi-pound' },
  { id: 'share', title: '预览与分享', glyph: 'mdi-pound' },
  { id: 'inner', title: '内部', glyph: 'mdi-lock-outline' },
]

beforeEach(() => {
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  setLocale('zh-CN')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount(tasks: RoomTask[], extra: Record<string, unknown> = {}) {
  return render(ProjectTasksView, {
    props: {
      tasks,
      channels,
      channelId: null,
      names: {},
      avatars: {},
      me: 'alice',
      loading: false,
      failed: false,
      closedTasks: [],
      closedCounts: null,
      closedHasMore: false,
      closedLoading: false,
      closedFailed: false,
      ...extra,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

const titles = (view: ReturnType<typeof mount>) =>
  Array.from(view.container.querySelectorAll('.tasks__title')).map((el) => el.textContent)

it('下一步在人手上的那一组排在最前面', () => {
  const building = task({ title: '在做的' })
  const waiting = task({ title: '等人的', presentation: { column: 'needs_you', phrase: 'awaiting_review' } })
  const checking = task({ title: '在检查的', presentation: { column: 'delivering', phrase: 'awaiting_checks' } })
  const view = mount([building, checking, waiting])
  expect(titles(view)).toEqual(['等人的', '在检查的', '在做的'])
})

it('等你的那一件在后面那一行说「等你」，两件的标签都只说状态', () => {
  const mine = task({
    title: '我的',
    owner_handle: 'alice',
    awaits_me: true,
    needs_my_action: true,
    presentation: { column: 'needs_you', phrase: 'awaiting_review' },
  })
  const theirs = task({ title: '别人的', presentation: { column: 'needs_you', phrase: 'awaiting_review' } })
  const view = mount([mine, theirs])
  // 等你的那一件只有一行写「等你」；两件的状态标签都是「待审阅」。
  expect(view.getAllByText(t('work.taskState.waitsOnYou'))).toHaveLength(1)
  expect(view.getAllByText(t('work.taskState.label.review'))).toHaveLength(2)
})

it('关注结果的人保留提醒，但不写成等待本人回答', () => {
  const view = mount(
    [
      task({
        title: '需要关注的任务',
        reporter_handle: 'alice',
        waiting_on: 'bob',
        awaits_me: true,
        needs_my_action: false,
        presentation: { column: 'needs_you', phrase: 'awaiting_answer' },
      }),
    ],
    { names: { alice: 'Alice', bob: 'Bob' } }
  )
  expect(view.queryByText(t('work.taskState.waitsOnYou'))).toBeNull()
  expect(view.getByText(t('work.taskState.waitsOn', { name: 'Bob' }))).toBeTruthy()
})

it('十四天没动静的收进「已停滞」，点开才列出来', async () => {
  const view = mount([task({ title: '还在动的' }), task({ title: '停住的', stalled: true })])
  expect(titles(view)).toEqual(['还在动的'])
  await fireEvent.click(view.getByRole('button', { name: new RegExp(t('work.projectTasks.stalled')) }))
  expect(titles(view)).toEqual(['还在动的', '停住的'])
})

it('选了频道就只列那个频道的任务', () => {
  const view = mount([task({ title: '前端的' }), task({ title: '分享的', room_id: 'share' })], { channelId: 'share' })
  expect(titles(view)).toEqual(['分享的'])
})

it('「我负责的」只列我是负责人的', async () => {
  const view = mount([task({ title: '我的', owner_handle: 'alice' }), task({ title: '别人的' })])
  await fireEvent.click(view.getByRole('button', { name: new RegExp(t('work.channelTasks.mine')) }))
  expect(titles(view)).toEqual(['我的'])
})

it('任务还没读到时，筛选上不写「0」', () => {
  const view = mount([], { loading: true })
  const chips = Array.from(view.container.querySelectorAll('.tasks__chips button')).map((el) => el.textContent ?? '')
  expect(chips.length).toBeGreaterThan(0)
  for (const chip of chips) expect(chip).not.toMatch(/\d/)
})

// 已关闭的只会越积越多：容器一页一页读（服务端筛好、数好），画面画它给的那一页。
it('看已关闭的：列的是读回来的那一页，筛选上写的是一共几件', async () => {
  const done = task({
    id: 'd1',
    title: '做完的',
    status: 'closed',
    presentation: { column: 'done', phrase: 'completed' },
  })
  const view = mount([], {
    status: 'closed',
    closedTasks: [done],
    closedCounts: { all: 120, mine: 40, helping: 3, others: 77 },
    closedHasMore: true,
  })

  expect(titles(view)).toEqual(['做完的'])
  expect(view.container.textContent).toContain('120')
  expect(view.getByTestId('tasks-more')).toBeTruthy()
})

it('频道菜单里每个频道只写名字，符号由图标画，私密频道是一把锁', async () => {
  const { getByTestId } = mount([])
  await fireEvent.click(getByTestId('tasks-channel'))

  const items = Array.from(document.querySelectorAll('[role="menuitem"]'))
  const front = items.find((item) => item.textContent?.includes('前端'))!
  expect(front.textContent?.trim()).toBe('前端')
  expect(front.querySelector('.mdi-pound')).not.toBeNull()
  const inner = items.find((item) => item.textContent?.includes('内部'))!
  expect(inner.querySelector('.mdi-lock-outline')).not.toBeNull()
})

it('全部状态同时列进行中与已关闭，并把两边的总数相加', () => {
  const view = mount([task({ title: '继续做', owner_handle: 'alice' })], {
    status: 'all',
    closedTasks: [
      task({
        title: '结束了',
        status: 'closed',
        presentation: { column: 'done', phrase: 'accepted' },
        accepted_at: '2026-10-01T00:00:00Z',
      }),
    ],
    closedCounts: { all: 120, mine: 40, helping: 3, others: 77 },
  })
  expect(titles(view)).toEqual(['继续做', '结束了'])
  expect(view.getByRole('button', { name: `${t('work.channelTasks.all')} 121` })).toBeTruthy()
  expect(view.getByRole('button', { name: `${t('work.channelTasks.mine')} 41` })).toBeTruthy()
  const row = view.getByRole('button', { name: /结束了/ })
  // 状态那一格自己说「已采纳」（不是「已关闭」），交付那一列另说交付被采纳。
  expect(within(row).getByTestId('task-state').textContent?.trim()).toBe(t('work.taskState.label.accepted'))
  expect(row.textContent).toContain(t('taskList.deliveryAccepted'))
})

it('状态是单选，重复点已关闭仍是已关闭，交付选择不随之改变', async () => {
  const view = mount([], { closedCounts: { all: 0, mine: 0, helping: 0, others: 0 } })
  const delivery = view.getByRole('button', { name: t('taskList.acceptedDelivery') })
  await fireEvent.click(delivery)
  const closed = view.getByRole('button', { name: t('work.channelTasks.closed') })
  await fireEvent.click(closed)
  await fireEvent.click(closed)
  expect(closed.getAttribute('aria-pressed')).toBe('true')
  expect(delivery.getAttribute('aria-pressed')).toBe('true')
  expect(view.getByRole('button', { name: t('taskList.open') }).getAttribute('aria-pressed')).toBe('false')
  await fireEvent.click(view.getByRole('button', { name: t('taskList.allStatuses') }))
  expect(closed.getAttribute('aria-pressed')).toBe('false')
  expect(delivery.getAttribute('aria-pressed')).toBe('true')
})

it('按采纳记录筛选，保留已采纳前几步和仍在收尾的进行中任务', async () => {
  const view = mount([
    task({ title: '前几步采纳了', accepted_at: '2026-10-01T00:00:00Z' }),
    task({
      title: '正在写总结',
      accepted_at: '2026-10-01T00:00:00Z',
      presentation: { column: 'done', phrase: 'accepted' },
    }),
    task({ title: '只写着采纳', presentation: { column: 'building', phrase: 'accepted' } }),
    task({ title: '没有采纳' }),
  ])
  await fireEvent.click(view.getByRole('button', { name: t('taskList.acceptedDelivery') }))
  expect(titles(view)).toEqual(['前几步采纳了', '正在写总结'])
  expect(view.getByRole('button', { name: /^全部 2$/ })).toBeTruthy()
  // 最后一步采纳后、任务还开着（在写总结）的那一会儿：还是在做。
  expect(view.getByRole('button', { name: /正在写总结/ }).textContent).toContain(t('work.taskState.label.running'))
  await fireEvent.click(view.getByRole('button', { name: t('taskList.anyDelivery') }))
  expect(titles(view)).toHaveLength(4)
})

it('采纳筛选仍与频道、负责人和停滞折叠组合', async () => {
  const accepted_at = '2026-10-01T00:00:00Z'
  const view = mount(
    [
      task({ title: '我的停滞交付', owner_handle: 'alice', accepted_at, stalled: true }),
      task({ title: '其他频道', room_id: 'share', owner_handle: 'alice', accepted_at }),
      task({ title: '别人的交付', accepted_at }),
      task({ title: '我的未采纳', owner_handle: 'alice' }),
    ],
    { channelId: 'front', delivery: 'accepted', whose: 'mine' }
  )
  expect(titles(view)).toEqual([])
  await fireEvent.click(view.getByRole('button', { name: new RegExp(t('work.projectTasks.stalled')) }))
  expect(titles(view)).toEqual(['我的停滞交付'])
  expect(view.getByRole('button', { name: /我的停滞交付/ }).textContent).toContain(t('taskList.deliveryAccepted'))
})

it('全部状态读关闭任务失败时，保留已读的进行中任务并能重试', async () => {
  const view = mount([task({ title: '还在做' })], { status: 'all', closedFailed: true })
  expect(titles(view)).toEqual(['还在做'])
  await fireEvent.click(view.getByRole('button', { name: t('global.loadError.retry') }))
  expect(view.emitted().more).toHaveLength(1)
})

it('全部状态为空且关闭任务读失败时，不把失败当成暂无任务', async () => {
  const view = mount([], { status: 'all', closedFailed: true })
  expect(view.queryByText(t('work.channelTasks.empty'))).toBeNull()
  expect(view.getByRole('alert').textContent).toContain(t('work.channelTasks.loadFailed'))
  await fireEvent.click(view.getByRole('button', { name: t('global.loadError.retry') }))
  expect(view.emitted().more).toHaveLength(1)
})

it('任务没单独指定队友时，卡住那一句叫它所在频道那位队友的名字，不叫默认的「芝士」', () => {
  const view = mount([task({ room_id: 'front', presentation: { column: 'needs_you', phrase: 'blocked' } })], {
    agentName: '芝士',
    roomAgentNames: { front: 'Nova' },
  })
  expect(view.container.textContent).toContain('打开看看Nova需要什么')
  expect(view.container.textContent).not.toContain('芝士')
})
