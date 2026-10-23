// 房间面板「例行事务」那一格（纯展示）：数据从 props 进，动作从事件出。
//
// 它是场景棘轮里的「场景」——panels/ 下的 SFC 新加的第一天就必须能脱离后端单独渲染。
// 这一份钉的就是这句话：给一组 props 出画面（删不掉的那一条不画「删除」），点「新建」
// 和存表单是把意图抛出去，不是自己去做。
import type { Routine } from '@/lib/routine'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import PanelRoutines from './PanelRoutines.vue'

import i18n, { setLocale } from '@/i18n'

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

const live: Routine = {
  id: 'live-1',
  can_delete: true,
  room_archived: false,
  project_id: 'p1',
  topic_id: 'room-1',
  title: '这个房间的日报',
  instructions: '把这个房间今天的进展写成一页',
  note: '',
  trigger: 'schedule',
  trigger_text: '每天 09:00（Asia/Shanghai）',
  spec: { freq: 'daily', time: '09:00' },
  timezone: 'Asia/Shanghai',
  state: 'active',
  agent_handle: 'cheese-x',
  owner_handle: 'u1',
  created_by: 'cheese-x',
  next_run_at: '2026-09-28T01:00:00Z',
  last_run_status: 'succeeded',
  credits_this_month: 0,
  revision: 1,
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
}

function mount(props: Record<string, unknown> = {}) {
  return render(PanelRoutines, {
    props: { routines: [live], defaultRoom: 'room-1', roomName: '周报房间', ...props },
    global: { plugins: [vuetify, i18n] },
  })
}

function button(label: string, scope: ParentNode = document.body): HTMLElement | undefined {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
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

describe('房间面板的「例行事务」（纯展示）', () => {
  it('给一组 props 出画面：删不掉的那一条，删除那一颗不画', async () => {
    const { container } = mount({ routines: [{ ...live, can_delete: false }] })
    const row = container.querySelector('[data-routine="live-1"]')!
    // 一行上动手的都在行尾那个 ⋯ 里；行里只剩名字、什么时候跑和状态。
    await fireEvent.click(row.querySelector<HTMLButtonElement>('button[aria-label="更多操作"]')!)
    await waitFor(() => expect(document.querySelectorAll('.v-overlay .v-list-item-title').length).toBeGreaterThan(0))
    const labels = Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((el) =>
      el.textContent?.trim()
    )
    expect(labels).not.toContain('删除')
    expect(labels).toContain('修改')
    expect(row.textContent).toContain('这个房间的日报')
  })

  it('点「新建」是把意图抛出去，不是自己开表单', async () => {
    const { emitted } = mount()
    await fireEvent.click(button('新建')!)
    expect(emitted()['start-new']).toHaveLength(1)
  })

  it('建规则那两页开着时：频道是定死的（不画选择框），存是把内容抛出去', async () => {
    const { emitted, container } = mount({ formOpen: true })
    await waitFor(() => expect(button('保存')).toBeTruthy())
    // 定死的那一个写在「频道」那一行上：看得到是哪个频道，但没有得挑 —— 这一页里
    // 没有一个写着频道名的下拉。
    expect(container.textContent).toContain('频道')
    expect(container.textContent).toContain('周报房间')
    expect(Array.from(container.querySelectorAll('.v-select')).some((el) => el.textContent?.includes('频道'))).toBe(
      false
    )

    // 目标必填：不填就不往下走（后端也这么判）。
    await fireEvent.click(button('保存')!)
    expect(emitted()['submit']).toBeUndefined()

    await fireEvent.update(container.querySelector('textarea')!, '把这个房间今天的进展写成一页')
    await fireEvent.click(button('保存')!)

    await waitFor(() => expect(emitted()['submit']).toBeTruthy())
    const [payload] = emitted()['submit'][0] as [{ room: string; keepAsTemplate: boolean }]
    expect(payload.room).toBe('room-1')
    expect(payload.keepAsTemplate).toBe(false)
  })

  it('模板那一页：预置五条，点一条把它铺进表里', async () => {
    const { emitted, container } = mount({ formOpen: true })
    await waitFor(() => expect(button('保存')).toBeTruthy())

    await fireEvent.click(
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === '模板')!
    )

    const presets = ['周报', '周测', '日报', '归档', '单元总结']
    for (const name of presets) expect(container.textContent).toContain(name)

    await fireEvent.click(
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('归档'))!
    )
    // 铺进来之后就翻回那张表：目标那一格写着这条模板要干什么。
    const goal = container.querySelector('textarea') as HTMLTextAreaElement
    expect(goal.value).toContain('归到该去的文件夹')

    // 「存成模板」那一颗：勾上就跟着这次保存一起抛出去。
    await check('存成模板')

    await fireEvent.click(button('保存')!)
    await waitFor(() => expect(emitted()['submit']).toBeTruthy())
    const [payload] = emitted()['submit'][0] as [
      { body: { trigger: string; spec: Record<string, unknown> }; keepAsTemplate: boolean },
    ]
    expect(payload.body.trigger).toBe('library_file_added')
    expect(payload.body.spec).toEqual({ scope: 'room' })
    expect(payload.keepAsTemplate).toBe(true)
  })
})
