/**
 * 桌面上频道页和任务页交给外壳布局区的样子。频道页旁边只有一处：工作面板的每一格带自己的标题栏，
 * 全在对话旁边那一格里，标题栏上的 ✕ 收起这一处。任务页「对话」那一种摆法里一样一块，各带标题栏：
 * 进来是概览，开别的概览就让开，几块能同时开着；编辑器那一种另有自己的摆法。
 */
import type { PagePanels, PagePanelsHandle } from '@/layouts/pagePanels'
import type { RoomLayoutOptions } from './roomLayout'

import { computed, defineComponent, h, nextTick, ref } from 'vue'
import { render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import { CENTRE_GROUP, CONVERSATION, useRoomLayout } from './roomLayout'

import { cellsOf, isSplit, placeTransient, reconcile } from '@/components/layout-area/arrangement'
import { NAVIGATOR, TERMINAL_BLOCK } from '@/components/work/workLayout'

/** 一个只记下页面交来了什么的外壳。 */
function setup(
  kind: 'room' | 'task' | 'editor',
  tabs?: RoomLayoutOptions['tabs'],
  workHidden = ref(false),
  scope = ref('t1')
) {
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
        { key: 'threads', title: '支线', icon: 'mdi-forum-outline' },
        { key: 'file:a.md', title: 'a.md', icon: 'mdi-file-outline', transient: true },
      ]),
    workHidden: () => workHidden.value,
    floating: () => false,
    hide: { label: () => '收起', run: hide },
    onActivated: () => {},
    onClose,
    onKeep: () => {},
    onSpawn: () => {},
    scope: () => scope.value,
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

describe('任务页的「对话」那一种摆法', () => {
  /** 此刻在屏幕上的那几块（没藏起来的），对话除外。 */
  const inSight = (declared: PagePanels) =>
    declared.panels.filter((one) => one.id !== CONVERSATION && !one.hidden).map((one) => one.id)
  /** 一块在哪一格：读者没拖过时，各格从左到右、从上到下数。 */
  const cellIndex = (declared: PagePanels, id: string) => laidOut(declared).findIndex((cell) => cell.includes(id))
  /** 一块在哪一栏：对话是第 0 栏。 */
  function columnOf(declared: PagePanels, id: string): number {
    const root = declared.arrangement
    if (!isSplit(root)) return -1
    return root.children.findIndex((child) =>
      isSplit(child)
        ? child.children.some((cell) => !isSplit(cell) && cell.panels.includes(id))
        : child.panels.includes(id)
    )
  }

  it('一样一块，各带自己的标题栏；对话没有标题栏也没有页签', () => {
    const { declared, work } = setup('task', TASK_TABS)
    expect(work.every((one) => one.titled)).toBe(true)
    expect(panel(declared, CONVERSATION)).toMatchObject({ stripWhenShared: true })
    expect(panel(declared, CONVERSATION)?.titled).toBeFalsy()
    expect(laidOut(declared).every((cell) => cell.length === 1)).toBe(true)
  })

  it('刚进来对话旁边是概览', () => {
    expect(inSight(setup('task', TASK_TABS).declared)).toEqual(['overview'])
  })

  it('打开文件，概览让开，文件那一块出来', () => {
    const { room, spec } = setup('task', TASK_TABS)
    room.workLayout.value!.show('code')
    expect(inSight(spec())).toEqual(['code'])
    expect(room.chatBlocks.value).toEqual({ overview: false, open: ['code'] })
  })

  it('文件和终端能同时开着：终端在文件下面，同一栏，矮一些', () => {
    const { room, spec } = setup('task', TASK_TABS)
    room.workLayout.value!.show('code')
    room.workLayout.value!.show('terminal:t1')
    const now = spec()
    expect(inSight(now)).toEqual(['code', TERMINAL_BLOCK])
    expect(columnOf(now, TERMINAL_BLOCK)).toBe(columnOf(now, 'code'))
    expect(cellIndex(now, TERMINAL_BLOCK)).toBeGreaterThan(cellIndex(now, 'code'))
    // 终端有自己的高度，不和上面那几块按比例分。
    expect(panel(now, TERMINAL_BLOCK)?.height).toBeTruthy()
  })

  it('只开着改动时它占对话右边那一栏；文件也开着时它去再往右的一栏', () => {
    const { room, spec } = setup('task', TASK_TABS)
    room.workLayout.value!.show('changes')
    const alone = spec()
    expect(inSight(alone)).toHaveLength(1)
    expect(columnOf(alone, inSight(alone)[0])).toBe(1)
    room.workLayout.value!.show('code')
    const both = spec()
    const shown = inSight(both)
    expect(shown).toHaveLength(2)
    expect(columnOf(both, 'code')).toBe(1)
    expect(columnOf(both, shown.find((id) => id !== 'code')!)).toBe(2)
  })

  it('概览叫回来时别的几块让开；再关掉概览，它们回来', () => {
    const { room, spec } = setup('task', TASK_TABS)
    room.workLayout.value!.show('code')
    room.workLayout.value!.show('overview')
    expect(inSight(spec())).toEqual(['overview'])
    room.closeBlock('overview')
    expect(inSight(spec())).toEqual(['code'])
  })

  it('最后一块关掉时对话占满，概览不自己回来；页头上的「概览」把它叫回来', () => {
    const workHidden = ref(false)
    const { room, spec, hide } = setup('task', TASK_TABS, workHidden)
    hide.mockImplementation(() => (workHidden.value = true))
    room.workLayout.value!.show('code')
    room.closeBlock('code')
    expect(hide).toHaveBeenCalledTimes(1)
    expect(inSight(spec())).toEqual([])
    // 页头上点「概览」：页面先打开工作面板那一侧，再显示概览。
    workHidden.value = false
    room.workLayout.value!.show('overview')
    expect(inSight(spec())).toEqual(['overview'])
  })

  it('标题栏上的 ✕ 只关那一块，工作面板听到的是它的名字，不管它摆在哪一栏', () => {
    const { room, spec, hide, onClose } = setup('task', TASK_TABS)
    room.workLayout.value!.show('code')
    room.workLayout.value!.show('changes')
    const changesAt = inSight(spec()).find((id) => id !== 'code')!
    spec().onClose!(changesAt)
    expect(onClose).toHaveBeenCalledWith('changes')
    expect(hide).not.toHaveBeenCalled()
    expect(inSight(spec())).toEqual(['code'])
  })

  it('换一件任务，回到只有概览', async () => {
    const scope = ref('t1')
    const { room, spec } = setup('task', TASK_TABS, ref(false), scope)
    room.workLayout.value!.show('code')
    scope.value = 't2'
    await nextTick()
    expect(inSight(spec())).toEqual(['overview'])
  })
})

describe('任务页的编辑器那一种摆法', () => {
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
    expect(setup('task', TASK_TABS).room.workLayout.value?.editor).toBeFalsy()
  })
})
