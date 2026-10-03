// 文档里等人决定的修改建议：还有几处、正看着哪一处、接受或拒绝。
//
// 建议就在协同文档里（lib/docSchema/suggestions.ts），这一层只读编辑器、跑编辑器上的
// 命令：接受和拒绝都是对正文的一次普通修改，跟着协同那一路到每个人那里。这一层不认识
// 接口，也不画东西。
import type { Editor } from '@tiptap/core'
import type { SuggestionRange } from '../lib/docSuggestionList'

import { computed, onScopeDispose, ref, watch } from 'vue'

import { scrollBehavior } from '@/utils/motion'

import { editMarks, setEditMarks } from '../lib/docEditMarks'
import { decideAllSuggestions, decideSuggestion, suggestionRanges } from '../lib/docSuggestionList'

export function useDocSuggestions(editorOf: () => Editor | null | undefined) {
  const tick = ref(0)
  const current = ref<string | null>(null)
  /** 这一次打开文档以来自己做的决定：都处理完时说一声各几处。 */
  const decided = ref({ accepted: 0, rejected: 0 })

  const list = computed<SuggestionRange[]>(() => {
    void tick.value
    const editor = editorOf()
    return editor && !editor.isDestroyed ? suggestionRanges(editor.state.doc) : []
  })
  const index = computed(() => list.value.findIndex((s) => s.id === current.value))
  const active = computed(() => (index.value >= 0 ? list.value[index.value] : null))

  function show(id: string | null) {
    current.value = id
    const editor = editorOf()
    if (!editor || editor.isDestroyed || editMarks(editor.state).suggestion === id) return
    editor.view.dispatch(setEditMarks(editor.state.tr, { suggestion: id }))
  }

  function reveal(range: SuggestionRange) {
    const editor = editorOf()
    if (!editor) return
    try {
      const { node } = editor.view.domAtPos(range.from)
      const el = node instanceof HTMLElement ? node : node.parentElement
      el?.scrollIntoView?.({ block: 'center', behavior: scrollBehavior() })
    } catch {
      // 位置刚被别人改掉：不滚。
    }
  }

  /** 上一处 / 下一处（-1 / 1），转到头就从另一头接着。 */
  function step(direction: -1 | 1) {
    const all = list.value
    if (!all.length) return
    const from = index.value < 0 ? (direction > 0 ? -1 : 0) : index.value
    const next = all[(from + direction + all.length) % all.length]
    show(next.id)
    reveal(next)
  }

  function focus(id: string) {
    if (list.value.some((s) => s.id === id)) show(id)
  }

  function decide(id: string, accept: boolean) {
    const editor = editorOf()
    if (!editor || editor.isDestroyed) return
    const at = list.value.findIndex((s) => s.id === id)
    if (!decideSuggestion(editor.state, editor.view.dispatch, id, accept)) return
    decided.value = {
      accepted: decided.value.accepted + (accept ? 1 : 0),
      rejected: decided.value.rejected + (accept ? 0 : 1),
    }
    // 接着看它后面那一处。
    const rest = list.value
    show(rest.length ? rest[Math.min(Math.max(at, 0), rest.length - 1)].id : null)
  }

  function decideAll(accept: boolean) {
    const editor = editorOf()
    if (!editor || editor.isDestroyed) return
    const count = list.value.length
    if (!count || !decideAllSuggestions(editor.state, editor.view.dispatch, accept)) return
    decided.value = {
      accepted: decided.value.accepted + (accept ? count : 0),
      rejected: decided.value.rejected + (accept ? 0 : count),
    }
    show(null)
  }

  function dismissDone() {
    decided.value = { accepted: 0, rejected: 0 }
  }

  // 正文里点到一处建议：看这一处。
  function onClick(e: MouseEvent) {
    const hit = (e.target as HTMLElement | null)?.closest<HTMLElement>('[data-suggestion]')
    if (hit?.dataset.suggestion) focus(hit.dataset.suggestion)
  }
  const onTransaction = () => {
    tick.value++
  }
  let bound: Editor | null = null
  function bind(editor: Editor | null | undefined) {
    if (bound === (editor ?? null)) return
    bound?.off('transaction', onTransaction)
    if (bound && !bound.isDestroyed) bound.view.dom.removeEventListener('click', onClick)
    bound = editor ?? null
    current.value = null
    dismissDone()
    tick.value++
    bound?.on('transaction', onTransaction)
    bound?.view.dom.addEventListener('click', onClick)
  }
  watch(editorOf, bind, { immediate: true })
  // 正看着的那一处被别人决定了：不再指着它。
  watch(list, (all) => {
    if (current.value && !all.some((s) => s.id === current.value)) show(null)
  })
  onScopeDispose(() => bind(null))

  return { list, current, index, active, decided, step, focus, decide, decideAll, dismissDone, close: () => show(null) }
}

export type DocSuggestionsController = ReturnType<typeof useDocSuggestions>
