// 文档内查找那一件事：查了什么、第几处、开没开。
//
// 匹配的计算在 lib/docFind.ts（纯函数，测试直接喂节点树）；这一层只拿着编辑器和这一
// 小块状态：把匹配高亮挂到编辑器上、在正文里滚到当前那一处、上下跳、开关。它不认识
// 接口，也不画东西 —— 画的是 DocFindBar.vue。
import type { Editor } from '@tiptap/core'
import type { Plugin } from '@tiptap/pm/state'
import type { FindMatch } from '../lib/docFind'

import { computed, onScopeDispose, ref, watch } from 'vue'

import { scrollBehavior } from '@/utils/motion'

import { clampIndex, findHighlightKey, findHighlightsPlugin, findMatches, stepIndex } from '../lib/docFind'

export function useDocFind(editorOf: () => Editor | null | undefined) {
  const query = ref('')
  const activeIndex = ref(0)
  const open = ref(false)
  /** 正文每变一次加一：匹配据它重算。 */
  const tick = ref(0)

  const matches = computed<FindMatch[]>(() => {
    void tick.value
    const editor = editorOf()
    if (!editor || editor.isDestroyed || !query.value) return []
    return findMatches(editor.state.doc, query.value)
  })
  const total = computed(() => matches.value.length)
  /** 当前是第几处（从 1 数）；没有匹配时是 0。 */
  const current = computed(() => (total.value ? clampIndex(activeIndex.value, total.value) + 1 : 0))

  /** 滚到当前那一处。 */
  function reveal() {
    const editor = editorOf()
    const match = matches.value[clampIndex(activeIndex.value, total.value)]
    if (!editor || editor.isDestroyed || !match) return
    try {
      const { node } = editor.view.domAtPos(match.from)
      const el = node instanceof Element ? node : node.parentElement
      const block = el?.closest<HTMLElement>('.ProseMirror > *') ?? (el as HTMLElement | null)
      block?.scrollIntoView?.({ block: 'center', behavior: scrollBehavior() })
    } catch {
      // 位置刚被别人改掉：不滚。
    }
  }

  // ---- 把高亮挂到编辑器上；换一份文档（换话题、重连）就换一次挂载。 ----
  let registered: Plugin | null = null
  let bound: Editor | null = null
  function unregister() {
    if (registered && bound && !bound.isDestroyed) bound.unregisterPlugin(findHighlightKey)
    registered = null
  }
  function bind(editor: Editor | null | undefined) {
    if (bound === (editor ?? null)) return
    unregister()
    bound?.off('transaction', onTransaction)
    bound = editor ?? null
    if (!bound) return
    bound.on('transaction', onTransaction)
    if (open.value) attach()
  }
  function attach() {
    if (!bound || bound.isDestroyed || registered) return
    registered = findHighlightsPlugin({ query: () => query.value, activeIndex: () => activeIndex.value })
    bound.registerPlugin(registered)
  }
  function poke() {
    const editor = bound
    if (registered && editor && !editor.isDestroyed)
      editor.view.dispatch(editor.state.tr.setMeta(findHighlightKey, true))
  }
  const onTransaction = () => {
    tick.value++
  }
  watch(editorOf, bind, { immediate: true })
  onScopeDispose(() => {
    unregister()
    bound?.off('transaction', onTransaction)
  })

  // 匹配变少时把当前下标夹回范围内（正文删了几处、查询改短了）。
  watch(total, (n) => {
    activeIndex.value = clampIndex(activeIndex.value, n)
  })

  /** 开 / 关查找条。开的时候挂上高亮，关的时候撤掉，并把焦点还给正文。 */
  function setOpen(value: boolean) {
    open.value = value
    if (value) attach()
    else {
      unregister()
      const editor = editorOf()
      if (editor && !editor.isDestroyed) editor.view.focus()
    }
  }
  function toggle() {
    setOpen(!open.value)
  }

  /** 改了查询：从头看起，滚到第一处。 */
  function setQuery(value: string) {
    query.value = value
    activeIndex.value = 0
    poke()
    reveal()
  }

  /** 下一处 / 上一处（1 / -1），到头绕回。 */
  function step(delta: number) {
    if (!total.value) return
    activeIndex.value = stepIndex(activeIndex.value, total.value, delta)
    poke()
    reveal()
  }

  return {
    query,
    open,
    matches,
    total,
    current,
    setQuery,
    step,
    setOpen,
    toggle,
  }
}
