/**
 * 桌面上频道页和任务页交给外壳布局区的样子。频道页旁边只有一处：工作面板的每一格带自己的标题栏，
 * 全在对话旁边那一格里，标题栏上的 ✕ 收起这一处。任务页照旧是能拖、能分屏的页签，✕ 关的是那一格。
 */
import type { PagePanels, PagePanelsHandle } from '@/layouts/pagePanels'
import type { RoomLayoutOptions } from './roomLayout'

import { computed, defineComponent, h } from 'vue'
import { render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import { CONVERSATION, useRoomLayout } from './roomLayout'

import { isSplit } from '@/components/layout-area/arrangement'

/** 一个只记下页面交来了什么的外壳。 */
function setup(kind: 'room' | 'task') {
  let spec: () => PagePanels | null = () => null
  const handle: PagePanelsHandle = {
    active: computed(() => true),
    outlet: () => undefined,
    headOutlet: () => undefined,
    maximized: computed(() => null),
    maximize: () => {},
    restore: () => {},
    visible: computed(() => new Set<string>()),
    show: () => {},
    rearranged: computed(() => false),
    reset: () => {},
  }
  const hide = vi.fn()
  const onClose = vi.fn()
  const opts: RoomLayoutOptions = {
    kind: () => kind,
    tabs: () => [
      { key: 'overview', title: '概览', icon: 'mdi-file-document-outline' },
      { key: 'threads', title: '支线', icon: 'mdi-forum-outline' },
      { key: 'file:a.md', title: 'a.md', icon: 'mdi-file-outline', transient: true },
    ],
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
  return { declared, work, hide, onClose, room }
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
