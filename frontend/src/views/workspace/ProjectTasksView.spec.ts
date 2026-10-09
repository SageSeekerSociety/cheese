// 全部任务：下一步在人手上的排最前；等你的写成「待你…」；十四天没动静的收进「已停滞」，
// 点开才列出来；选了频道就只列那个频道的任务。
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
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
  { id: 'front', title: '前端' },
  { id: 'share', title: '预览与分享' },
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
  const fresh = task({ title: '没开始的', presentation: { column: 'not_started', phrase: 'discussing' } })
  const view = mount([building, fresh, waiting])
  expect(titles(view)).toEqual(['等人的', '在做的', '没开始的'])
})

it('等你的那一件写成「待你…」，等别人的照通用的说法', () => {
  const mine = task({
    title: '我的',
    owner_handle: 'alice',
    awaits_me: true,
    presentation: { column: 'not_started', phrase: 'discussing' },
  })
  const theirs = task({ title: '别人的', presentation: { column: 'not_started', phrase: 'discussing' } })
  const view = mount([mine, theirs])
  expect(view.getByText(t('work.board.mine.discussing'))).toBeTruthy()
  expect(view.getByText(t('work.board.phrase.discussing'))).toBeTruthy()
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
    closed: true,
    closedTasks: [done],
    closedCounts: { all: 120, mine: 40, helping: 3, others: 77 },
    closedHasMore: true,
  })

  expect(titles(view)).toEqual(['做完的'])
  expect(view.container.textContent).toContain('120')
  expect(view.getByTestId('tasks-more')).toBeTruthy()
})
