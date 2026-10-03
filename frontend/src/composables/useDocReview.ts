// 「查看改动」：某个人让 AI 队友改的那几处，在文档里一处处看，哪一处不要就还原。
//
// 改动从房间里那条通知来（它带着每一处的原文和新文）；这一层在正文里把它们找出来、标
// 上，还原就是以自己的名义把新文换回原文 —— 请求本身由上面递进来，这一层不认识接口。
// 新文已经不在正文里的那一处（还原过了，或者后来被人改掉了）算作已还原。
import type { Editor } from '@tiptap/core'
import type { DocEdit } from '../lib/docEdits'
import type { DocReviewRequest, LocatedEdit } from '../lib/docReview'

import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'

import { scrollBehavior } from '@/utils/motion'

import { editMarks, setEditMarks } from '../lib/docEditMarks'
import { editFailure } from '../lib/docEdits'
import { locateEdits } from '../lib/docReview'

export interface DocReviewOptions {
  editor: () => Editor | null | undefined
  applyEdits: () => ((edits: DocEdit[]) => Promise<unknown>) | undefined
  onError: (message: string) => void
}

export function useDocReview(options: DocReviewOptions) {
  const request = shallowRef<DocReviewRequest | null>(null)
  /** 正看着的那一处（在请求里的序号）。 */
  const active = ref<number | null>(null)
  const busy = ref(false)
  const tick = ref(0)
  // 刚打开、正文还没到：到了就看第一处。
  let pendingFirst = false

  const located = computed<LocatedEdit[]>(() => {
    void tick.value
    const editor = options.editor()
    const req = request.value
    return editor && !editor.isDestroyed && req ? locateEdits(editor.state.doc, req.edits) : []
  })
  const live = computed(() => located.value.filter((c) => c.live))
  const current = computed(() => live.value.find((c) => c.index === active.value) ?? null)

  function paint() {
    const editor = options.editor()
    if (!editor || editor.isDestroyed) return
    const req = request.value
    const now = editMarks(editor.state).review
    const next = req ? { edits: req.edits, active: active.value } : null
    if (now?.edits === next?.edits && now?.active === next?.active) return
    editor.view.dispatch(setEditMarks(editor.state.tr, { review: next }))
  }

  function reveal(change: LocatedEdit) {
    const editor = options.editor()
    if (!editor) return
    try {
      const { node } = editor.view.domAtPos(change.from)
      const el = node instanceof HTMLElement ? node : node.parentElement
      el?.scrollIntoView?.({ block: 'center', behavior: scrollBehavior() })
    } catch {
      // 位置刚被别人改掉：不滚。
    }
  }

  function open(next: DocReviewRequest) {
    request.value = next
    active.value = null
    pendingFirst = true
    paint()
    showFirst()
  }

  function showFirst() {
    const first = live.value[0]
    if (!pendingFirst || !first) return
    pendingFirst = false
    active.value = first.index
    paint()
    reveal(first)
  }

  function close() {
    request.value = null
    active.value = null
    busy.value = false
    pendingFirst = false
    paint()
  }

  function step(direction: -1 | 1) {
    const all = live.value
    if (!all.length) return
    const at = all.findIndex((c) => c.index === active.value)
    const from = at < 0 ? (direction > 0 ? -1 : 0) : at
    const next = all[(from + direction + all.length) % all.length]
    active.value = next.index
    paint()
    reveal(next)
  }

  function focus(index: number) {
    if (!live.value.some((c) => c.index === index)) return
    active.value = index
    paint()
  }

  /** 还原这处：把新文换回原文，以自己的名义。 */
  async function restore(index: number) {
    const apply = options.applyEdits()
    const change = located.value.find((c) => c.index === index)
    if (!apply || !change || busy.value) return
    const req = request.value
    busy.value = true
    try {
      await apply([{ old: change.edit.new, new: change.edit.old }])
    } catch (error) {
      if (request.value === req) {
        options.onError(editFailure(error))
      }
    } finally {
      if (request.value === req) busy.value = false
    }
  }

  // 正文点到一处改动：看这一处。
  function onClick(e: MouseEvent) {
    const hit = (e.target as HTMLElement | null)?.closest<HTMLElement>('[data-review]')
    if (hit?.dataset.review) focus(Number(hit.dataset.review))
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
    tick.value++
    bound?.on('transaction', onTransaction)
    bound?.view.dom.addEventListener('click', onClick)
  }
  // 编辑器换了一个（换了房间由上面关掉；同一篇重建时改动还在）：标记画到新的上面。
  watch(
    options.editor,
    (editor) => {
      bind(editor)
      paint()
    },
    { immediate: true }
  )
  // 正看着的那一处被还原了：接着看下一处。
  watch(live, (all) => {
    if (pendingFirst) return showFirst()
    if (active.value === null || all.some((c) => c.index === active.value)) return
    const next = all.find((c) => c.index > (active.value ?? -1)) ?? all[0] ?? null
    active.value = next?.index ?? null
    paint()
  })
  onScopeDispose(() => bind(null))

  return { request, active, busy, located, live, current, open, close, step, focus, restore }
}

export type DocReviewController = ReturnType<typeof useDocReview>
