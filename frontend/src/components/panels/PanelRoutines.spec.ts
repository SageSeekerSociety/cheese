// 房间面板「例行事务」那一格（纯展示）：数据从 props 进，动作从事件出。
//
// 它是场景棘轮里的「场景」——panels/ 下的 SFC 新加的第一天就必须能脱离后端单独渲染。
// 这一份钉的就是这句话：给一组 props 出画面（做不到的按钮不出现、也没有一句解释），
// 点「新建」和存表单是把意图抛出去，不是自己去做。
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
  can_manage: true,
  room_archived: false,
  project_id: 'p1',
  topic_id: 'room-1',
  title: '这个房间的日报',
  instructions: '把这个房间今天的进展写成一页',
  output_dir: '日报',
  trigger: 'schedule',
  trigger_text: '每天 09:00（Asia/Shanghai）',
  spec: { freq: 'daily', time: '09:00' },
  scope_topic_ids: [],
  scope_topics: [],
  last_run: null,
  run_count: 0,
  timezone: 'Asia/Shanghai',
  state: 'active',
  agent_handle: 'cheese-x',
  owner_handle: 'u1',
  proposed_by: 'cheese-x',
  confirmed_by: 'u1',
  confirmed_at: '2026-09-25T02:00:00Z',
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
  // 频道里的人都能暂停、能立即执行一次，所以这一行有 ⋯；做不到的（确认启用、删除）
  // 不出现，也不写一句「只有谁能做」——菜单里到底有哪几样，钉在 RoutineRow.spec 里。
  it('给一组 props 出画面：动作收在行尾的 ⋯ 里，不写一句「只有谁能做」', () => {
    const { container } = mount({ routines: [{ ...live, can_manage: false, owner_handle: 'u-other' }] })
    const row = container.querySelector('[data-routine="live-1"]')!
    expect(row.querySelectorAll('button')).toHaveLength(1)
    expect(row.querySelector('button')!.getAttribute('aria-label')).toBe('更多操作')
    expect(row.textContent).not.toContain('只有他和项目管理员能改')
  })

  it('点「新建」是把意图抛出去，不是自己开表单', async () => {
    const { emitted } = mount()
    await fireEvent.click(button('新建')!)
    expect(emitted()['start-new']).toHaveLength(1)
  })

  // 新建那张表只有一段大白话：名字、工作内容、存储位置、执行条件都由 AI 从这段话里
  // 起草，之后才摊在「待确认」那一条里。这一格里连「执行范围」都不画——规则就长在这个
  // 房间，没有第二个答案。
  it('表单开着时：只有一段大白话，存是把这段话和这个房间抛出去', async () => {
    const { emitted } = mount({ formOpen: true })
    await waitFor(() => expect(button('提交')).toBeTruthy())
    expect(document.body.textContent).toContain('例行事务与执行条件')
    expect(document.body.textContent).not.toContain('执行范围')
    expect(document.body.textContent).not.toContain('存储位置')

    const brief = document.querySelector('.v-dialog textarea')!
    await fireEvent.update(brief, '每天下班前把这个房间今天的进展写成一页')

    await fireEvent.click(button('提交')!)
    await waitFor(() => expect(emitted()['submit']).toBeTruthy())
    const [payload] = emitted()['submit'][0] as [{ room: string; body: { instructions: string; spec: unknown } }]
    expect(payload.room).toBe('room-1')
    expect(payload.body.instructions).toBe('每天下班前把这个房间今天的进展写成一页')
    expect(payload.body.spec).toEqual({})
  })
})
