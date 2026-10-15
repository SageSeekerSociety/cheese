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
  revision: 1,
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
}

function mount(props: Record<string, unknown> = {}) {
  return render(PanelRoutines, {
    props: { routines: [live], defaultRoom: 'room-1', ...props },
    global: { plugins: [vuetify, i18n] },
  })
}

function button(label: string, scope: ParentNode = document.body): HTMLElement | undefined {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
}

describe('房间面板的「例行事务」（纯展示）', () => {
  it('给一组 props 出画面：删不掉的那一条，删除那一颗不画', () => {
    const { container } = mount({ routines: [{ ...live, can_delete: false }] })
    const row = container.querySelector('[data-routine="live-1"]')!
    const labels = Array.from(row.querySelectorAll('button')).map((b) => b.textContent?.trim())
    expect(labels).not.toContain('删除')
    expect(labels).toContain('修改')
    expect(row.textContent).toContain('这个房间的日报')
  })

  it('点「新建」是把意图抛出去，不是自己开表单', async () => {
    const { emitted } = mount()
    await fireEvent.click(button('新建')!)
    expect(emitted()['start-new']).toHaveLength(1)
  })

  it('表单开着时：房间是定死的（不问「在哪个房间执行」），存是把内容抛出去', async () => {
    const { emitted } = mount({ formOpen: true })
    await waitFor(() => expect(button('保存')).toBeTruthy())
    expect(document.body.textContent).not.toContain('在哪个房间执行')

    await fireEvent.click(button('保存')!)
    await waitFor(() => expect(emitted()['submit']).toBeTruthy())
    const [payload] = emitted()['submit'][0] as [{ room: string }]
    expect(payload.room).toBe('room-1')
  })
})
