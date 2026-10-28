// 房间右侧「例行事务」那一格的取数外壳：**只问这一个房间**。
//
// 它和项目「例行事务」页画的是同一串（`RoutineBoard`，经 `panels/PanelRoutines.vue`）、
// 填的是同一张表，差别只有范围：那一页是整个项目，这一格带着 `topicId` 去问后端。所以
// 这一份钉的是三件只有这一格才成立的事：问的是这个房间、新建的规则长在这个房间（表单
// 里连「在哪个房间执行」都不问）、删不掉的那一条不画「删除」。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

vi.mock('../../api/routines', () => ({
  listProjectRoutines: vi.fn(),
  getRoutine: vi.fn(),
  listRoutineRuns: vi.fn(),
  createRoutine: vi.fn(),
  updateRoutine: vi.fn(),
  routineAction: vi.fn(),
  deleteRoutine: vi.fn(),
  listRoutineTemplates: vi.fn(),
  createRoutineTemplate: vi.fn(),
  deleteRoutineTemplate: vi.fn(),
}))

import RoutinePanelHost from './RoutinePanelHost.vue'

const { createRoutine, createRoutineTemplate, getRoutine, listProjectRoutines, listRoutineTemplates, updateRoutine } =
  await import('../../api/routines')

const vuetify = createVuetify({ components, directives })

beforeEach(() => setLocale('zh-CN'))

afterEach(cleanup)

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
  // 行尾的 ⋯ 走 Vuetify 的浮层定位，它读 devicePixelRatio；jsdom 里没有这个全局。
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
})

const live = {
  id: 'live-1',
  can_delete: true,
  room_archived: false,
  project_id: 'p1',
  topic_id: 'room-1',
  title: '这个房间的日报',
  instructions: '把这个房间今天的进展写成一页',
  note: '',
  trigger: 'schedule' as const,
  trigger_text: '每天 09:00（Asia/Shanghai）',
  spec: { freq: 'daily', time: '09:00' },
  timezone: 'Asia/Shanghai',
  state: 'active' as const,
  last_run_status: 'succeeded' as const,
  credits_this_month: 0,
  agent_handle: 'cheese-x',
  owner_handle: 'u1',
  created_by: 'cheese-x',
  next_run_at: '2026-09-28T01:00:00Z',
  revision: 1,
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listProjectRoutines).mockResolvedValue({ data: [live], total: 1 })
  // 模板存在项目里（`api/routines.ts` 那三个接口），默认这个项目一条也没有。
  vi.mocked(listRoutineTemplates).mockResolvedValue({ data: [], total: 0 })
})

function mount() {
  return render(RoutinePanelHost, {
    props: { topicId: 'room-1', projectId: 'p1' },
    global: { plugins: [vuetify, i18n] },
  })
}

function button(label: string, scope: ParentNode = document.body): HTMLElement | undefined {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
}

/** 行尾那个 ⋯ 里此刻有哪几件。一行上动手的都在那儿，行里只剩名字、什么时候跑和状态。 */
async function rowActions(row: Element): Promise<string[]> {
  await fireEvent.click(row.querySelector<HTMLButtonElement>('button[aria-label="更多操作"]')!)
  await waitFor(() => expect(document.querySelectorAll('.v-overlay .v-list-item-title').length).toBeGreaterThan(0))
  return Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map(
    (el) => el.textContent?.trim() as string
  )
}

/** 勾上一颗勾选框（「时间」「条件」「存成模板」），按它那一行写着什么字认。
 *  Vuetify 的事件挂在里面那颗 `input` 上、且只听 `input`，所以自己把它拨上再派一次。 */
async function check(label: string) {
  const box = Array.from(document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')).find((i) =>
    i.closest('.v-input')?.textContent?.includes(label)
  )!
  box.checked = true
  await fireEvent.input(box)
}

describe('房间右侧的「例行事务」', () => {
  it('问的是这一个房间，不是整个项目', async () => {
    const { findByText } = mount()
    await findByText('这个房间的日报')
    expect(listProjectRoutines).toHaveBeenCalledWith('p1', 'room-1')
  })

  it('还没有规则：写的是「点新建」，不是一个「暂无」', async () => {
    vi.mocked(listProjectRoutines).mockResolvedValue({ data: [], total: 0 })
    const { findByText } = mount()
    await findByText('还没有例行事务')
  })

  it('一句话里那个名字点名了这一条（?routine=）：这一格开在它上面', async () => {
    vi.mocked(getRoutine).mockResolvedValue({ ...live, runs: [] })
    const { findByText } = render(RoutinePanelHost, {
      props: { topicId: 'room-1', projectId: 'p1', routineId: 'live-1' },
      global: { plugins: [vuetify, i18n] },
    })
    await findByText('这个房间的日报')
    // 选中它、展开它最近三次 —— 和人在行上点一下是同一件事。
    await waitFor(() => expect(getRoutine).toHaveBeenCalledWith('live-1'))
  })

  it('点名的那一条已经删了：什么都不开，也不报错', async () => {
    const { findByText } = render(RoutinePanelHost, {
      props: { topicId: 'room-1', projectId: 'p1', routineId: 'gone-1' },
      global: { plugins: [vuetify, i18n] },
    })
    await findByText('这个房间的日报')
    expect(getRoutine).not.toHaveBeenCalled()
  })

  it('删不掉的那一条：⋯ 里没有删除，别的动作还在', async () => {
    vi.mocked(listProjectRoutines).mockResolvedValue({
      data: [{ ...live, can_delete: false, owner_handle: 'u-other' }],
      total: 1,
    })
    const { container, findByText } = mount()
    await findByText('这个房间的日报')
    const labels = await rowActions(container.querySelector('[data-routine="live-1"]')!)
    expect(labels).not.toContain('删除')
    expect(labels).toContain('修改')
  })

  it('在这一格里新建：频道是定死的，目标不填存不下去', async () => {
    vi.mocked(createRoutine).mockResolvedValue({ ...live, id: 'new-1' })
    const { findByText, container } = mount()
    await findByText('这个房间的日报')

    await fireEvent.click(button('新建')!)
    await waitFor(() => expect(button('保存')).toBeTruthy())

    // 目标必填（后端也这么判）：空着点保存，一次请求都不发，就地写一句为什么。
    await fireEvent.click(button('保存')!)
    expect(createRoutine).not.toHaveBeenCalled()
    await waitFor(() => expect(container.textContent).toContain('目标要填'))

    await fireEvent.update(container.querySelector('textarea')!, '把这个房间今天的进展写成一页')
    await fireEvent.click(button('保存')!)

    await waitFor(() => expect(createRoutine).toHaveBeenCalled())
    const [room, body] = vi.mocked(createRoutine).mock.calls[0]
    expect(room).toBe('room-1')
    // 没选触发方式：空着交出去，由这个房间的队友照目标补（不替人挑一个）。
    expect(body.trigger).toBe('')
    expect(body.spec).toEqual({})
  })

  it('勾了「存成模板」：规则存下来之后，「项目里存的」里多一条，下次能从它开始', async () => {
    vi.mocked(createRoutine).mockResolvedValue({ ...live, id: 'new-1' })
    vi.mocked(createRoutineTemplate).mockResolvedValue({
      id: 'tpl-1',
      title: '把这个房间今天的进展写成一页',
      goal: '把这个房间今天的进展写成一页',
      note: '',
      trigger: 'schedule',
      spec: { freq: 'weekly', time: '09:00', weekdays: [0] },
    })
    const { findByText, container } = mount()
    await findByText('这个房间的日报')

    await fireEvent.click(button('新建')!)
    await waitFor(() => expect(button('保存')).toBeTruthy())
    await fireEvent.update(container.querySelector('textarea')!, '把这个房间今天的进展写成一页')
    await check('存成模板')
    await fireEvent.click(button('保存')!)

    // 存规则和存模板是两件事：这一次保存要把这张表也交给项目。
    await waitFor(() => expect(createRoutineTemplate).toHaveBeenCalled())
    const [project, saved] = vi.mocked(createRoutineTemplate).mock.calls[0]
    expect(project).toBe('p1')
    expect(saved.goal).toBe('把这个房间今天的进展写成一页')

    // 再开一次：模板那一页上有刚存的那一条。
    await fireEvent.click(button('新建')!)
    await waitFor(() => expect(button('保存')).toBeTruthy())
    await fireEvent.click(
      Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.trim() === '模板')!
    )
    await waitFor(() => expect(document.body.textContent).toContain('项目里存的'))
    expect(document.body.textContent).toContain('把这个房间今天的进展写成一页')
  })

  it('改一条每日分诊的钟点：它附带的反馈条数跟着留下，不被表单丢掉', async () => {
    const triage = { ...live, spec: { freq: 'daily', time: '09:00', feedback_batch: 5 } }
    vi.mocked(listProjectRoutines).mockResolvedValue({ data: [triage], total: 1 })
    vi.mocked(updateRoutine).mockResolvedValue(triage)
    const { findByText } = mount()
    await findByText('这个房间的日报')

    const row = document.querySelector('[data-routine="live-1"]')!
    await fireEvent.click(row.querySelector<HTMLButtonElement>('button[aria-label="更多操作"]')!)
    await waitFor(() => expect(document.querySelector('.v-overlay .v-list-item-title')).toBeTruthy())
    await fireEvent.click(
      Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).find(
        (el) => el.textContent?.trim() === '修改'
      ) as HTMLElement
    )
    await waitFor(() => expect(button('保存')).toBeTruthy())
    await fireEvent.click(button('保存')!)

    await waitFor(() => expect(updateRoutine).toHaveBeenCalled())
    const body = vi.mocked(updateRoutine).mock.calls[0][1]
    expect(body.spec).toEqual({ freq: 'daily', time: '09:00', feedback_batch: 5 })
  })
})
