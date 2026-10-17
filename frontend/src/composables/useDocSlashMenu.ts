// 文档里 slash 菜单的状态 —— 打「/」挑块类型时，菜单开在哪、列着什么、停在第几行；
// 以及「/」是 ＋ 手柄种下的那一种情况下，菜单没选就走了怎么收尾。
//
// 菜单的候选表和它写进正文的东西住在 lib/docSlashMenu.ts，浮层住在
// components/panels/doc/DocSlashMenu.vue；这里只有「它现在开在哪」这一份状态。
// 状态在这里而不在浮层那一半，因为建议插件的回调是建编辑器时一口气接上的 ——
// 谁建编辑器谁得拿着这几个回调。
import type { SuggestionProps } from '@tiptap/suggestion'
import type { SlashItem } from '../lib/docSlashMenu'

import { ref } from 'vue'

import { caretMenuPos } from './useDocRefMenu'

export interface SlashMenuState {
  items: SlashItem[]
  index: number
  top: number
  left: number
}

export function useDocSlashMenu() {
  const menu = ref<SlashMenuState | null>(null)
  // Latest suggestion props — command() routes through the plugin so the
  // "/query" range is deleted consistently for keyboard and mouse picks.
  let current: SuggestionProps<SlashItem, SlashItem> | null = null
  // Set by the ＋ handle: it inserted the "/" itself. If the menu closes while
  // that "/" is still alone in its paragraph, we remove it again (Notion does
  // exactly this — the slash was UI scaffolding, not user content).
  let planted = false

  function show(p: SuggestionProps<SlashItem, SlashItem>) {
    current = p
    const pos = caretMenuPos(p.clientRect, Math.min(p.items.length, 8) * 33 + 10)
    if (!pos || p.items.length === 0) {
      menu.value = null // no matches → menu hides, "/query" stays as text
      return
    }
    // Selection resets to the top on every keystroke (Notion behaviour).
    menu.value = { items: p.items, index: 0, top: pos.top, left: pos.left }
  }

  function run(item: SlashItem) {
    current?.command(item)
  }

  function onKeyDown({ event }: { event: KeyboardEvent }): boolean {
    const m = menu.value
    if (!m) return false // Escape is handled by the plugin itself (exits)
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const delta = event.key === 'ArrowDown' ? 1 : -1
      m.index = (m.index + delta + m.items.length) % m.items.length
      return true
    }
    if (event.key === 'Enter') {
      run(m.items[m.index])
      return true
    }
    return false
  }

  function onExit(p: SuggestionProps<SlashItem, SlashItem>) {
    menu.value = null
    current = null
    if (!planted) return
    planted = false
    // The ＋ button planted this "/" — if the menu closed without a pick and
    // nothing else was typed, the paragraph still reads exactly "/": clean it.
    try {
      const ed = p.editor
      const $from = ed.state.doc.resolve(p.range.from)
      if ($from.parent.type.name === 'paragraph' && $from.parent.textContent === '/') {
        ed.commands.deleteRange({ from: p.range.from, to: p.range.from + 1 })
      }
    } catch {
      // stale range (block moved/removed) — nothing to clean
    }
  }

  // 浮层里发生的事。鼠标挑一项走的是和键盘同一条 command()；悬停只改选中行。
  function hover(index: number) {
    if (menu.value) menu.value.index = index
  }

  function markPlanted() {
    planted = true
  }

  return { menu, show, run, onKeyDown, onExit, hover, markPlanted }
}
