// 文档大纲那一件事：正文里有哪些标题、点了跳到哪。
//
// 抽取在 lib/docOutline.ts（纯函数）；这一层拿着编辑器，把标题在正文变化后重算，并把
// 点击落成一次滚动。它不画东西 —— 画的是 DocOutline.vue。
import type { Editor } from '@tiptap/core'

import { computed, onScopeDispose, ref, watch } from 'vue'

import { scrollBehavior } from '@/utils/motion'

import { extractOutline } from '../lib/docOutline'

export function useDocOutline(editorOf: () => Editor | null | undefined) {
  const tick = ref(0)
  const headings = computed(() => {
    void tick.value
    const editor = editorOf()
    return editor && !editor.isDestroyed ? extractOutline(editor.state.doc) : []
  })

  /** 滚到某一节（节点位置由大纲项的 `pos` 给出）。 */
  function go(pos: number) {
    const editor = editorOf()
    if (!editor || editor.isDestroyed) return
    try {
      const dom = editor.view.nodeDOM(pos)
      const el = dom instanceof HTMLElement ? dom : dom?.parentElement ?? null
      el?.scrollIntoView({ block: 'start', behavior: scrollBehavior() })
    } catch {
      // 位置刚被别人改掉：不滚。
    }
  }

  const onTransaction = () => {
    tick.value++
  }
  let bound: Editor | null = null
  function bind(editor: Editor | null | undefined) {
    if (bound === (editor ?? null)) return
    bound?.off('transaction', onTransaction)
    bound = editor ?? null
    tick.value++
    bound?.on('transaction', onTransaction)
  }
  watch(editorOf, bind, { immediate: true })
  onScopeDispose(() => {
    bound?.off('transaction', onTransaction)
  })

  return { headings, go }
}
