// 一条规则那一行：**按钮亮不亮由这条规则自己说**。
//
// 后端逐条算好 `can_manage`（规则主人或项目管理员），前端照着画。权限是三档，不是两
// 档：频道里的人都能改、能暂停/恢复、能立即执行一次；只有规则主人和项目管理员能确认
// 启用、能删除。做不到的按钮干脆不出现，也不写一句「只有谁能做」。
//
// 已经在跑的那一条，动作只有一处：行尾那颗 ⋯（桌面上右键一行弹的是同一份，弹在鼠标
// 那一点上）。待确认的那一条例外——「确认启用」是它唯一要紧的事，摆在行里。
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
  output_dir: '周报',
  trigger: 'schedule',
  trigger_text: '每周一 09:00（Asia/Shanghai）',
  i18n: {
    trigger_text: {
      key: 'routineTriggerWeekly',
      params: {
        days: { list: [{ key: 'routineWeekdayMon', params: {} }], quoted: false },
        time: '09:00',
        timezone: 'Asia/Shanghai',
      },
    },
  },
  spec: { freq: 'weekly', weekdays: [0], time: '09:00' },
  scope_topic_ids: [],
  scope_topics: [],
  last_run: null,
  run_count: 12,
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

/** 此刻弹出来的菜单里那几行。 */
function menuItems(): (string | null)[] {
  return Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((el) => el.textContent?.trim() ?? null)
}

/** 这一行上画出来的按钮（桌面上那一排文字按钮；手机上收进 ⋯，那里没有按钮）。 */
function buttons(container: Element): string[] {
  return Array.from(container.querySelectorAll('button'))
    .map((b) => b.textContent?.trim() ?? '')
    .filter((s) => s.length > 0)
}

describe('一条规则这一行', () => {
  it('changes the trigger language on an already rendered row, not its timezone or clock', async () => {
    const { container } = mount(base)
    expect(container.textContent).toContain('每周一 09:00（Asia/Shanghai）')
    setLocale('en')
    await waitFor(() => {
      expect(container.textContent).toContain('Every Monday at 09:00 (Asia/Shanghai)')
      expect(container.textContent).not.toContain('每周一')
    })
  })

  it('右键这一行：弹出行尾 ⋯ 里那几样操作，弹在鼠标那一点上', async () => {
    const { container } = mount(base)
    await fireEvent.contextMenu(container.querySelector('.routine-row')!, { clientX: 20, clientY: 40 })
    await waitFor(() =>
      expect(
        Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((el) => el.textContent?.trim())
      ).toEqual(['暂停', '立即执行一次', '修改', '执行记录', '删除'])
    )
  })

  it('已经在跑的：五样动作都收在行尾那颗 ⋯ 里，行里不再摊一排按钮', async () => {
    const ui = mount(base)
    expect(buttons(ui.container)).toEqual([])
    expect(ui.container.textContent).toContain('每周一 09:00（Asia/Shanghai）')
    expect(ui.container.textContent).toContain('执行中')
    await fireEvent.click(ui.getByRole('button', { name: '更多操作' }))
    await waitFor(() => expect(menuItems()).toEqual(['暂停', '立即执行一次', '修改', '执行记录', '删除']))
  })

  it('暂停了的：动作是「恢复」，不是「暂停」', async () => {
    const ui = mount({ ...base, state: 'paused' })
    expect(ui.container.textContent).toContain('已暂停')
    await fireEvent.click(ui.getByRole('button', { name: '更多操作' }))
    await waitFor(() => {
      expect(menuItems()).toContain('恢复')
      expect(menuItems()).not.toContain('暂停')
    })
  })

  it('等确认的草稿：把它要做什么摊开，动作是「确认启用」', () => {
    const { container } = mount({ ...base, state: 'draft', next_run_at: null })
    expect(buttons(container)).toEqual(['确认启用', '修改', '删除'])
    expect(container.textContent).toContain('汇总本周完成的任务和进行中的事')
    expect(container.textContent).toContain('周报')
    expect(container.textContent).toContain('执行范围')
    // 没勾频道就是「未限定」——「执行范围」这一行永远在，空着不说话会让人以为没读到。
    expect(container.textContent).toContain('未限定')
  })

  it('等确认的草稿：勾了频道就把它们的名字列出来', () => {
    const { container } = mount({
      ...base,
      state: 'draft',
      next_run_at: null,
      scope_topic_ids: ['room-1', 'room-2'],
      scope_topics: [
        { id: 'room-1', title: '设计' },
        { id: 'room-2', title: '前端' },
      ],
    })
    expect(container.textContent).toContain('设计')
    expect(container.textContent).toContain('前端')
    expect(container.textContent).not.toContain('未限定')
  })

  // 频道里的人能改、能暂停、能立即执行一次，所以别人的规则上仍然有 ⋯；只有「删除」
  // 不出现。少一颗按钮就够了，不弹提示、也不写一句「只有谁能做」。
  it('别人的规则：能做的照样有，做不到的不出现，也不写一句解释', async () => {
    const ui = mount({ ...base, can_manage: false, owner_handle: 'u-other' })
    expect(ui.container.textContent).not.toContain('只有他和项目管理员能改')
    await fireEvent.click(ui.getByRole('button', { name: '更多操作' }))
    await waitFor(() => expect(menuItems()).toEqual(['暂停', '立即执行一次', '修改', '执行记录']))
  })

  // 待确认的那一条只有「确认启用」「修改」；「删除」也归负责人和管理员。
  it('别人的待确认：只有「修改」', () => {
    const { container } = mount({ ...base, state: 'draft', can_manage: false, next_run_at: null })
    expect(buttons(container)).toEqual(['修改'])
  })

  // 归档不是暂停：规则自己的状态没变（取消归档后从下一个时刻继续），所以这里说的是
  // 「随归档停止」，而不是把这一条说成「已暂停」。
  it('房间归档了：说「已随频道归档停止」，不说「上次执行 …」', () => {
    const { container } = mount({ ...base, room_archived: true })
    expect(container.textContent).toContain('已随频道归档停止')
    expect(container.textContent).not.toContain('上次执行')
    expect(container.textContent).not.toContain('还没有执行过')
    expect(container.textContent).toContain('执行中')
  })

  it('展开执行记录：一次没跑成写「未能执行」，原因折在「原因」里', () => {
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
    expect(container.textContent).toContain('未能执行')
    expect(container.textContent).toContain('原因')
    expect(container.textContent).toContain('没有交回结果')
  })

  // 例行事务里填的「存储位置 周报/」只是这些文件的落脚路径，频道面板上没有对应的文件夹 ——
  // 这一格里的每一份都得当场点得开，不然它们在任何地方都打不开。
  it('房间里那一格：展开执行记录，交回的结果文件点得开', async () => {
    const ui = mount(base, {
      open: true,
      runs: [
        {
          id: 'run-1',
          routine_id: 'r-1',
          trigger_detail: '计划时间 2026-09-28 09:00。',
          routine_revision: 1,
          scheduled_for: '2026-09-28T01:00:00Z',
          status: 'succeeded',
          summary: '已汇总',
          outputs: ['周报/2026-10-11.md'],
          error: '',
          created_at: '2026-09-28T01:00:00Z',
          started_at: '2026-09-28T01:00:00Z',
          finished_at: '2026-09-28T01:10:00Z',
        },
      ],
    })
    // 标签就是整条路径：这一格和上面那些频道面板在同一屏，路径本身是它唯一的出处。
    const file = ui.getByRole('button', { name: '周报/2026-10-11.md' })
    await fireEvent.click(file)
    expect(ui.emitted()['open-file']).toEqual([['周报/2026-10-11.md']])
  })

  // 项目页上没有能开文件的面板：那一页整串指回规则所在的房间，不画成死的路径。
  it('项目页：结果文件整串指回规则所在的房间', () => {
    const ui = mount(base, {
      open: true,
      roomTo: { name: 'workspace-topic', params: { projectId: 'p1', topicId: 'room-1' } },
      runs: [
        {
          id: 'run-1',
          routine_id: 'r-1',
          trigger_detail: '计划时间 2026-09-28 09:00。',
          routine_revision: 1,
          scheduled_for: '2026-09-28T01:00:00Z',
          status: 'succeeded',
          summary: '已汇总',
          outputs: ['周报/2026-10-11.md'],
          error: '',
          created_at: '2026-09-28T01:00:00Z',
          started_at: '2026-09-28T01:00:00Z',
          finished_at: '2026-09-28T01:10:00Z',
        },
      ],
    })
    const file = ui.container.querySelector('a.routine-run__file')
    expect(file).not.toBeNull()
    expect(file?.textContent).toContain('周报/2026-10-11.md')
  })
})
