/**
 * 桌面上频道页和任务页交给外壳布局区的样子。频道页旁边只有一处：工作面板的每一格带自己的标题栏，
 * 全在对话旁边那一格里，标题栏上的 ✕ 收起这一处。任务页照旧是能拖、能分屏的页签，✕ 关的是那一格。
 */
import type { PagePanels, PagePanelsHandle } from '@/layouts/pagePanels'
import type { RoomLayoutOptions } from './roomLayout'

import { computed, defineComponent, h } from 'vue'
import { render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import { CENTRE_GROUP, CONVERSATION, useRoomLayout } from './roomLayout'

import { cellsOf, isSplit, placeTransient, reconcile } from '@/components/layout-area/arrangement'
import { NAVIGATOR } from '@/components/work/workLayout'

/** 一个只记下页面交来了什么的外壳。 */
function setup(kind: 'room' | 'task' | 'editor', tabs?: RoomLayoutOptions['tabs']) {
  let spec: () => PagePanels | null = () => null
  const shown: string[] = []
  const handle: PagePanelsHandle = {
    active: computed(() => true),
    outlet: () => undefined,
    headOutlet: () => undefined,
    maximized: computed(() => null),
    maximize: () => {},
    restore: () => {},
    visible: computed(() => new Set<string>()),
    show: (id) => shown.push(id),
    rearranged: computed(() => false),
    reset: () => {},
  }
  const hide = vi.fn()
  const onClose = vi.fn()
  const opts: RoomLayoutOptions = {
    kind: () => kind,
    tabs:
      tabs ??
      (() => [
        { key: 'overview', title: '概览', icon: 'mdi-file-document-outline' },
        { key: 'threads', title: '讨论串', icon: 'mdi-forum-outline' },
        { key: 'file:a.md', title: 'a.md', icon: 'mdi-file-outline', transient: true },
      ]),
    workHidden: () => false,
    floating: () => false,
    hide: { label: () => '收起', run: hide },
    onActivated: () => {},
    onClose,
    onKeep: () => {},
    onSpawn: () => {},
  }
  let room!: ReturnType<typeof useRoomLayout>
  render(
    defineComponent({
      setup() {
        room = useRoomLayout((s) => {
          spec = s
          return handle
        }, opts)
        return () => h('div')
      },
    })
  )
  const declared = spec()!
  const work = declared.panels.filter((panel) => panel.id !== CONVERSATION)
  return { declared, work, hide, onClose, room, spec: () => spec()!, shown }
}

describe('频道页', () => {
  it('工作面板的每一格带自己的标题栏，固定的几格默认在对话旁边同一格里', () => {
    const { declared, work } = setup('room')
    expect(work.every((panel) => panel.titled)).toBe(true)
    const root = declared.arrangement
    expect(isSplit(root) && root.children.map((child) => (isSplit(child) ? null : child.panels))).toEqual([
      [CONVERSATION],
      ['overview', 'threads'],
    ])
  })

  it('标题栏上的 ✕ 收起这一处，不管是哪一格', () => {
    const { declared, hide, onClose } = setup('room')
    declared.onClose!('overview')
    declared.onClose!('file:a.md')
    expect(hide).toHaveBeenCalledTimes(2)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('交给工作面板的是一次显示一格', () => {
    expect(setup('room').room.workLayout.value?.single).toBe(true)
  })
})

describe('任务页', () => {
  it('照旧是页签：没有标题栏，✕ 关的是那一格', () => {
    const { declared, work, hide, onClose } = setup('task')
    expect(work.some((panel) => panel.titled)).toBe(false)
    declared.onClose!('file:a.md')
    expect(onClose).toHaveBeenCalledWith('file:a.md')
    expect(hide).not.toHaveBeenCalled()
  })

  it('交给工作面板的不是一次显示一格', () => {
    expect(setup('task').room.workLayout.value?.single).toBe(false)
  })
})

describe('任务页的编辑器那一种摆法', () => {
  const TASK_TABS: RoomLayoutOptions['tabs'] = () => [
    { key: 'overview', title: '概览', icon: 'mdi-file-document-outline' },
    { key: 'site', title: '现场', icon: 'mdi-hammer-wrench' },
    { key: 'changes', title: '改动', icon: 'mdi-source-branch' },
    { key: 'preview', title: '预览', icon: 'mdi-eye-outline' },
    { key: 'code', title: '文件', icon: 'mdi-folder-outline', closable: true },
    { key: 'terminal', title: '终端', icon: 'mdi-console', spawns: true, terminal: true },
    { key: 'terminal:t1', title: '终端', icon: 'mdi-console', transient: true, terminal: true },
  ]
  /** 外壳照页面的话摆出来的那几格（读者没拖过）：每一格里有哪几块，按从左到右、从上到下。 */
  function laidOut(declared: PagePanels): string[][] {
    const groupOf = (id: string) => declared.panels.find((panel) => panel.id === id)?.group
    const ids = declared.panels.filter((panel) => !panel.spawns).map((panel) => panel.id)
    let arrangement = reconcile({ root: declared.arrangement }, ids)
    for (const panel of declared.panels.filter((one) => one.transient)) {
      arrangement = placeTransient(arrangement, panel, groupOf)
    }
    return cellsOf(arrangement).map((cell) => cell.panels)
  }
  const panel = (declared: PagePanels, id: string) => declared.panels.find((one) => one.id === id)

  it('左边是文件栏，中间是打开的文件，终端在它下面，对话在右边', () => {
    const { declared } = setup('editor', TASK_TABS)
    expect(laidOut(declared)).toEqual([[NAVIGATOR], ['code'], ['terminal:t1'], [CONVERSATION]])
    const root = declared.arrangement
    expect(isSplit(root) && root.direction).toBe('row')
    // 两边各自有宽度，拖过就记住；中间那一格占剩下的。
    expect(panel(declared, NAVIGATOR)?.width).toBeTruthy()
    expect(panel(declared, CONVERSATION)?.width).toBeTruthy()
    expect(panel(declared, 'code')?.width).toBeUndefined()
  })

  it('概览、现场、改动、预览不常摆着，要显示哪一样时才打开在中间，成为能关的一格', () => {
    const { declared, room, spec, shown } = setup('editor', TASK_TABS)
    expect(declared.panels.map((one) => one.id)).not.toContain('changes')
    room.workLayout.value!.show('changes')
    const now = spec()
    expect(shown).toContain('changes')
    expect(panel(now, 'changes')).toMatchObject({ group: CENTRE_GROUP, closable: true })
    expect(laidOut(now)).toEqual([[NAVIGATOR], ['code', 'changes'], ['terminal:t1'], [CONVERSATION]])
    now.onClose!('changes')
    expect(spec().panels.map((one) => one.id)).not.toContain('changes')
  })

  it('页头上的三颗各自藏起、再摆回左栏、终端和对话', () => {
    const { room, spec } = setup('editor', TASK_TABS)
    const hidden = () =>
      spec()
        .panels.filter((one) => one.hidden)
        .map((one) => one.id)
    expect(hidden()).toEqual([])
    room.toggleEditorPart('left')
    expect(hidden()).toEqual([NAVIGATOR])
    room.toggleEditorPart('left')
    room.toggleEditorPart('terminal')
    expect(hidden()).toEqual(expect.arrayContaining(['terminal:t1']))
    expect(hidden()).not.toContain(NAVIGATOR)
    room.toggleEditorPart('terminal')
    room.toggleEditorPart('conversation')
    expect(hidden()).toEqual([CONVERSATION])
    room.toggleEditorPart('conversation')
    expect(hidden()).toEqual([])
  })

  it('三块藏没藏记在这台设备上，下次打开还是那样', () => {
    setup('editor', TASK_TABS).room.toggleEditorPart('conversation')
    const again = setup('editor', TASK_TABS)
    expect(panel(again.spec(), CONVERSATION)?.hidden).toBe(true)
    again.room.toggleEditorPart('conversation')
  })

  it('交给工作面板的是编辑器那一种', () => {
    expect(setup('editor', TASK_TABS).room.workLayout.value?.editor).toBe(true)
    expect(setup('task', TASK_TABS).room.workLayout.value?.editor).toBe(false)
  })
})
