// 一条规则那一行：**按钮亮不亮由这条规则自己说**。
//
// 后端逐条算好 `can_manage`（规则主人或项目管理员），前端照着画。这一份钉的就是这句
// 话的两半：拿得到权限时动作都在，拿不到时一颗按钮都不画、而且写清为什么 —— 一条没
// 有按钮的规则和一条不允许你动的规则，看起来不该是同一个东西。
import type { Component } from 'vue'
import type { Routine } from '@/lib/routine'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import RoutineRow from './RoutineRow.vue'

import i18n, { setLocale } from '@/i18n'

const Row = RoutineRow as unknown as Component
const vuetify = createVuetify({ components, directives })

beforeEach(() => setLocale('zh-CN'))

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // 右键弹出的菜单是 VOverlay，定位要读这两样，happy-dom 没有。
  if (!globalThis.visualViewport) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1280,
      height: 800,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    }
  }
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
})

const base: Routine = {
  id: 'r-1',
  can_manage: true,
  room_archived: false,
  project_id: 'p1',
  topic_id: 'room-1',
  title: '每周项目进展',
  instructions: '汇总本周完成的任务和进行中的事',
  context_scope: '本项目所有房间的任务',
  output_dir: '周报',
  trigger: 'schedule',
  trigger_text: '每周周一 09:00（Asia/Shanghai）',
  spec: { freq: 'weekly', weekdays: [0], time: '09:00' },
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

function mount(routine: Routine, props: Record<string, unknown> = {}) {
  return render(Row, { props: { routine, ...props }, global: { plugins: [vuetify, i18n] } })
}

/** 这一行上画出来的按钮（桌面上那一排文字按钮；手机上收进 ⋯，那里没有按钮）。 */
function buttons(container: Element): string[] {
  return Array.from(container.querySelectorAll('button'))
    .map((b) => b.textContent?.trim() ?? '')
    .filter((s) => s.length > 0)
}

describe('一条规则这一行', () => {
  it('右键这一行：弹出行里那几样操作', async () => {
    const { container } = mount(base)
    await fireEvent.contextMenu(container.querySelector('.routine-row')!, { clientX: 20, clientY: 40 })
    await waitFor(() =>
      expect(
        Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((el) => el.textContent?.trim())
      ).toEqual(['暂停', '立即执行一次', '修改', '执行记录', '删除'])
    )
  })

  it('已经在跑的：暂停、立即执行一次、修改、执行记录、删除都在', () => {
    const { container } = mount(base)
    expect(buttons(container)).toEqual(['暂停', '立即执行一次', '修改', '执行记录', '删除'])
    expect(container.textContent).toContain('每周周一 09:00（Asia/Shanghai）')
    expect(container.textContent).toContain('执行中')
  })

  it('暂停了的：动作是「恢复」，不是「暂停」', () => {
    const { container } = mount({ ...base, state: 'paused' })
    expect(buttons(container)).toContain('恢复')
    expect(buttons(container)).not.toContain('暂停')
    expect(container.textContent).toContain('已暂停')
  })

  it('等确认的草稿：把它要做什么摊开，动作是「确认启用」', () => {
    const { container } = mount({ ...base, state: 'draft', next_run_at: null })
    expect(buttons(container)).toEqual(['确认启用', '修改', '不要了'])
    expect(container.textContent).toContain('汇总本周完成的任务和进行中的事')
    expect(container.textContent).toContain('本项目所有房间的任务')
    expect(container.textContent).toContain('周报')
  })

  it('别人的规则：一颗按钮都不画，并且说清归谁管', () => {
    const { container } = mount({ ...base, can_manage: false, owner_handle: 'u-other' })
    expect(buttons(container)).toEqual([])
    expect(container.textContent).toContain('u-other')
    expect(container.textContent).toContain('只有他和项目管理员能改')
  })

  // 归档不是暂停：规则自己的状态没变（取消归档后从下一个时刻继续），所以这里说的是
  // 「随归档停止」，而不是把这一条说成「已暂停」。
  it('房间归档了：说「已随话题归档停止」，不说「下次 …」', () => {
    const { container } = mount({ ...base, room_archived: true })
    expect(container.textContent).toContain('已随频道归档停止')
    expect(container.textContent).not.toContain('下次')
    expect(container.textContent).toContain('执行中')
  })

  it('展开执行记录：一次失败说出原因，一次成功说出做了什么', () => {
    const { container } = mount(base, {
      open: true,
      runs: [
        {
          id: 'run-1',
          routine_id: 'r-1',
          trigger_detail: '计划时间 2026-09-28 09:00。',
          routine_revision: 1,
          scheduled_for: '2026-09-28T01:00:00Z',
          status: 'failed',
          summary: '',
          outputs: [],
          error: 'AI 队友这一轮已经结束，但没有交回结果',
          created_at: '2026-09-28T01:00:00Z',
          started_at: '2026-09-28T01:00:00Z',
          finished_at: '2026-09-28T01:10:00Z',
        },
      ],
    })
    expect(container.textContent).toContain('失败')
    expect(container.textContent).toContain('没有交回结果')
  })
})
