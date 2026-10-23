// 一条规则那一行。钉三件事：
//
//   - **删得掉删不掉由这条规则自己说**：后端逐条算好 `can_delete`（规则主人或项目管理
//     员），前端照着画，别的动手都不问第二遍权限。
//   - **状态是图形不是字**：四档各一颗（`routineStateIcon`），读屏靠 `aria-label`。
//   - **记录摊开只画最近三次**：再多给「看全部」，那条路通到整页。
import type { Component } from 'vue'
import type { Routine, RoutineRun } from '@/lib/routine'

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
  can_delete: true,
  room_archived: false,
  project_id: 'p1',
  topic_id: 'room-1',
  title: '每周项目进展',
  instructions: '汇总本周完成的任务和进行中的事',
  note: '结果放在「周报」目录',
  trigger: 'schedule',
  trigger_text: '每周周一 09:00（Asia/Shanghai）',
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
  timezone: 'Asia/Shanghai',
  state: 'active',
  last_run_status: 'succeeded',
  credits_this_month: 128.4,
  agent_handle: 'cheese-x',
  owner_handle: 'u1',
  created_by: 'cheese-x',
  next_run_at: '2026-09-28T01:00:00Z',
  revision: 1,
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
}

function mount(routine: Routine, props: Record<string, unknown> = {}) {
  return render(Row, { props: { routine, ...props }, global: { plugins: [vuetify, i18n] } })
}

/** 行尾 ⋯ 里那一份清单。桌面上是 v-menu，点上才画出来。 */
async function openMenu(container: Element): Promise<string[]> {
  const more = container.querySelector<HTMLButtonElement>('button[aria-label="更多操作"]')
  if (!more) throw new Error('这一行上没有 ⋯')
  await fireEvent.click(more)
  await waitFor(() => {
    expect(document.querySelectorAll('.v-overlay .v-list-item-title').length).toBeGreaterThan(0)
  })
  return Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((el) =>
    el.textContent?.trim()
  ) as string[]
}

/** 行上那颗状态图形说的是什么（读屏听到的那一句）。 */
function stateLabel(container: Element): string | null {
  return container.querySelector('.routine-row__state')?.getAttribute('aria-label') ?? null
}

function run(over: Partial<RoutineRun>): RoutineRun {
  return {
    id: 'run-1',
    routine_id: 'r-1',
    trigger_detail: '',
    routine_revision: 1,
    scheduled_for: '2026-09-28T01:00:00Z',
    status: 'succeeded',
    summary: '已汇总',
    outputs: [],
    error: '',
    created_at: '2026-09-28T01:00:00Z',
    started_at: '2026-09-28T01:00:00Z',
    finished_at: '2026-09-28T01:10:00Z',
    ...over,
  }
}

describe('一条规则这一行', () => {
  it('changes the trigger language on an already rendered row, not its timezone or clock', async () => {
    const { container } = mount(base)
    expect(container.textContent).toContain('每周周一 09:00（北京时间）')
    setLocale('en')
    await waitFor(() => {
      expect(container.textContent).toContain('Every Monday at 09:00 (Beijing time)')
      expect(container.textContent).not.toContain('每周周一')
    })
  })

  it('启用中的：⋯ 里是暂停、立即执行一次、修改、执行记录、删除', async () => {
    const { container } = mount(base)
    expect(await openMenu(container)).toEqual(['暂停', '立即执行一次', '修改', '执行记录', '删除'])
    expect(container.textContent).toContain('每周周一 09:00（北京时间）')
  })

  it('右键这一行：弹的是同一份清单', async () => {
    const { container } = mount(base)
    await fireEvent.contextMenu(container.querySelector('.routine-row')!, { clientX: 20, clientY: 40 })
    await waitFor(() =>
      expect(
        Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((el) => el.textContent?.trim())
      ).toEqual(['暂停', '立即执行一次', '修改', '执行记录', '删除'])
    )
  })

  // 行上不写状态的字：图形就是「它怎么了」本身，颜色是跟着它走的第二层。
  it('状态是一颗图形：启用中用暂停那两颗，读屏听到「启用中」', () => {
    const { container } = mount(base)
    expect(stateLabel(container)).toBe('启用中')
    expect(container.querySelector('.routine-row__state--active')).not.toBeNull()
    expect(container.textContent).not.toContain('启用中')
  })

  it('暂停了的：动作是「恢复」，图形是三角，读屏听到「已暂停」', async () => {
    const { container } = mount({ ...base, state: 'paused' })
    expect(stateLabel(container)).toBe('已暂停')
    expect(container.querySelector('.routine-row__state--paused')).not.toBeNull()
    expect(await openMenu(container)).toContain('恢复')
    expect(await openMenu(container)).not.toContain('暂停')
  })

  it('还没定触发方式的：没有「恢复」，让它跑起来的是「修改」', async () => {
    const { container } = mount({ ...base, state: 'paused', trigger: '', trigger_text: '还没定什么时候跑' })
    const items = await openMenu(container)
    expect(items).not.toContain('恢复')
    expect(items).toContain('修改')
  })

  // 这两档不在规则自己身上（`state` 只知道启用 / 暂停）：正在跑的那条画沙漏，上一次
  // 没跑成的那条画红叉 —— 都得看最近一次执行跑成什么样。
  it('最近一次在排队：画沙漏，不看规则自己的状态', () => {
    const { container } = mount({ ...base, last_run_status: 'queued' })
    expect(stateLabel(container)).toBe('排队中，这就跑')
    expect(container.querySelector('.routine-row__state--queued')).not.toBeNull()
  })

  it('最近一次没能执行：画红叉', () => {
    const { container } = mount({ ...base, last_run_status: 'failed' })
    expect(stateLabel(container)).toBe('上一次没能执行')
    expect(container.querySelector('.routine-row__state--missed')).not.toBeNull()
  })

  // 停着的规则先说话：它不会跑了，最近那次跑成什么样都是过去的事。
  it('暂停了的：最近一次没跑成也不画红叉', () => {
    const { container } = mount({ ...base, state: 'paused', last_run_status: 'failed' })
    expect(container.querySelector('.routine-row__state--paused')).not.toBeNull()
    expect(container.querySelector('.routine-row__state--missed')).toBeNull()
  })

  // 一条别人的规则里，只有「删除」收窄到规则主人和项目管理员；暂停、立即执行、修改
  // 读得到就做得了，所以那一项单独不见。
  it('删不掉的那一条：别的动作都在，就是没有「删除」', async () => {
    const { container } = mount({ ...base, can_delete: false })
    expect(await openMenu(container)).toEqual(['暂停', '立即执行一次', '修改', '执行记录'])
  })

  // 归档不是暂停：规则自己的状态没变（取消归档后从下一个时刻继续），所以这里说的是
  // 「随归档停止」，而不是把这一条说成「已暂停」。
  it('房间归档了：说「已随频道归档停止」，不说「下次 …」', () => {
    const { container } = mount({ ...base, room_archived: true })
    expect(container.textContent).toContain('已随频道归档停止')
    expect(container.textContent).not.toContain('下次')
    expect(stateLabel(container)).toBe('启用中')
  })

  it('项目页的行：名字底下那一行是「房间名 · 什么时候跑」', () => {
    const { container } = mount(base, { roomName: '课程设计' })
    expect(container.querySelector('.routine-row__id')?.textContent).toContain('课程设计 · 每周周一 09:00（北京时间）')
  })

  it('展开执行记录：没能执行的那几次把原因收在「原因 ▸」后面', async () => {
    const { container } = mount(base, {
      open: true,
      runs: [run({ status: 'failed', summary: '', error: '工作电脑没有连上' })],
    })
    expect(container.textContent).toContain('失败')
    expect(container.textContent).not.toContain('工作电脑没有连上')
    await fireEvent.click(container.querySelector('.routine-run__why')!)
    expect(container.textContent).toContain('工作电脑没有连上')
  })

  // 记录里写的「周报/2026-10-11.md」只是这次交回的文件路径，频道面板上没有对应的文件夹 ——
  // 这一格里的每一份都得当场点得开，不然它们在任何地方都打不开。
  it('房间里那一格：展开执行记录，交回的结果文件点得开', async () => {
    const ui = mount(base, { open: true, runs: [run({ outputs: ['周报/2026-10-11.md'] })] })
    const file = ui.getByRole('button', { name: '2026-10-11.md' })
    expect(file.getAttribute('title')).toBe('周报/2026-10-11.md')
    await fireEvent.click(file)
    expect(ui.emitted()['open-file']).toEqual([['周报/2026-10-11.md']])
  })

  // 项目页上没有能开文件的面板：那几份整串指回规则所在的房间，不画成死的路径。
  it('项目页：结果文件整串指回规则所在的房间', () => {
    const ui = mount(base, {
      open: true,
      roomTo: { name: 'workspace-topic', params: { projectId: 'p1', topicId: 'room-1' } },
      runs: [run({ outputs: ['周报/2026-10-11.md'] })],
    })
    expect(ui.container.querySelector('.routine-run__file[title]')).toBeNull()
    expect(ui.container.querySelector('.routine-run__what')?.textContent).toContain('2026-10-11.md')
  })

  // 行里只摊开最近三次。再多的那几次不画在这里，但要说清它们在哪儿 —— 不然第四条往
  // 后的记录只有「重新打开这一整页」一条路。
  it('记录超过三次：行里只画三条，底下给「看全部」', async () => {
    const ui = mount(base, {
      open: true,
      runs: [run({ id: 'run-1' }), run({ id: 'run-2' }), run({ id: 'run-3' }), run({ id: 'run-4' })],
    })
    expect(ui.container.querySelectorAll('.routine-run__what')).toHaveLength(3)
    await fireEvent.click(ui.getByRole('button', { name: '看全部' }))
    expect(ui.emitted()['open-records']).toHaveLength(1)
  })

  it('刚好三次：不画「看全部」', () => {
    const ui = mount(base, { open: true, runs: [run({ id: 'run-1' }), run({ id: 'run-2' }), run({ id: 'run-3' })] })
    expect(ui.container.querySelector('.routine-runs__all')).toBeNull()
  })
})
