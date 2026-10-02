// 「让{agent}改」：选中一段字，写一句要求，AI 队友直接改掉，人可以撤销。
//
// 这一层只管这一次修改走到哪一步，和正文上画什么；请求本身由上面递进来（取数在
// usePanelDoc），所以它和画它的组件都不认识接口。
//
// 改好的字不从回执里取：服务端改的是协同文档，新的字和别人打的字一样从协同那一路到
// 编辑器。回执说的是「改成了什么」，这一层在正文里找到它，亮一下，再把条子放在旁边。
import type { Editor } from '@tiptap/core'
import type { EditTarget } from '../lib/docEditMarks'
import type { DocEdit, DocRewriteRequest, DocRewriteResult } from '../lib/docEdits'

import { onScopeDispose, ref, shallowRef } from 'vue'

import { editMarks, nearestText, setEditMarks } from '../lib/docEditMarks'
import { plainOf } from '../lib/docEdits'
import { rewriteTarget } from '../lib/docRewrite'
import { finishMarkdown } from '../lib/docSchema'

import { t } from '@/i18n'

export type RewritePhase = 'idle' | 'asking' | 'pending' | 'done' | 'undone'

export interface DocRewriteOptions {
  editor: () => Editor | null | undefined
  rewrite: () => ((request: DocRewriteRequest) => Promise<DocRewriteResult>) | undefined
  applyEdits: () => ((edits: DocEdit[]) => Promise<unknown>) | undefined
  agentName: () => string
  onError: (message: string) => void
}

/** 改好的字最多等这么久才到；到不了也不再等，条子照样给。 */
const ARRIVAL_MS = 10_000
/** 新的字亮多久。 */
const FLASH_MS = 2400

function serializer(editor: Editor) {
  return (doc: Parameters<typeof rewriteTarget>[0]['doc']) =>
    finishMarkdown(editor.markdown?.serialize(doc.toJSON()) ?? '')
}

function failure(error: unknown): string {
  if ((error as { status?: number } | null)?.status === 409) return t('work.room.docEdit.stale')
  if (error instanceof Error && error.message) return t('work.room.docEdit.failed', { reason: error.message })
  return t('work.room.docEdit.failedRetry')
}

export function useDocRewrite(options: DocRewriteOptions) {
  const phase = ref<RewritePhase>('idle')
  const busy = ref(false)
  const result = shallowRef<DocRewriteResult | null>(null)
  // 每开一次、关一次就换一个号：晚到的回执对不上号就不认。
  let attempt = 0
  let stopWaiting: (() => void) | null = null
  let flashTimer: ReturnType<typeof setTimeout> | undefined

  function mark(editor: Editor, target: EditTarget | null) {
    if (editor.isDestroyed) return
    editor.view.dispatch(setEditMarks(editor.state.tr, { target }))
  }
  function target(editor: Editor) {
    return editMarks(editor.state).target
  }
  function settle() {
    stopWaiting?.()
    stopWaiting = null
    if (flashTimer) clearTimeout(flashTimer)
    flashTimer = undefined
  }

  /** 选中的那一段开出输入框。 */
  function open(from: number, to: number) {
    const editor = options.editor()
    if (!editor || !options.rewrite()) return
    attempt++
    settle()
    result.value = null
    phase.value = 'asking'
    mark(editor, { from, to, mode: 'select', label: '' })
  }

  function close() {
    attempt++
    settle()
    phase.value = 'idle'
    result.value = null
    busy.value = false
    const editor = options.editor()
    if (editor && target(editor)) mark(editor, null)
  }

  /** 在正文里找到改好的字，亮一下；找不到就停在原处。 */
  function land(editor: Editor, replacement: string, id: number) {
    const near = target(editor)
    const found = nearestText(editor.state.doc, plainOf(replacement), near?.from ?? 0)
    if (!found) return false
    if (id !== attempt) return true
    mark(editor, { ...found, mode: 'flash', label: '' })
    phase.value = 'done'
    flashTimer = setTimeout(() => {
      const now = target(editor)
      if (id === attempt && now?.mode === 'flash') mark(editor, { ...now, mode: 'anchor' })
    }, FLASH_MS)
    return true
  }

  function awaitArrival(editor: Editor, replacement: string, id: number) {
    if (land(editor, replacement, id)) return
    const onChange = ({ transaction }: { transaction: { docChanged: boolean } }) => {
      if (transaction.docChanged && land(editor, replacement, id)) settle()
    }
    const timer = setTimeout(() => {
      settle()
      if (id !== attempt) return
      const now = target(editor)
      if (now) mark(editor, { ...now, mode: 'anchor' })
      phase.value = 'done'
    }, ARRIVAL_MS)
    editor.on('transaction', onChange)
    stopWaiting = () => {
      editor.off('transaction', onChange)
      clearTimeout(timer)
    }
  }

  async function send(instruction: string) {
    const editor = options.editor()
    const rewrite = options.rewrite()
    const range = editor && target(editor)
    const text = instruction.trim()
    if (!editor || !rewrite || !range || !text || phase.value !== 'asking') return
    const request = rewriteTarget(editor.state, range.from, range.to, serializer(editor))
    if (!request) {
      close()
      options.onError(t('work.room.docEdit.unmappable'))
      return
    }
    const id = ++attempt
    phase.value = 'pending'
    mark(editor, { ...range, mode: 'pending', label: t('work.room.docEdit.pending', { agent: options.agentName() }) })
    try {
      const done = await rewrite({ block: request.block, start: request.start, end: request.end, instruction: text })
      if (id !== attempt) return
      result.value = done
      awaitArrival(editor, done.replacement, id)
    } catch (error) {
      if (id !== attempt) return
      close()
      options.onError(failure(error))
    }
  }

  async function swap(edit: DocEdit, next: RewritePhase) {
    const apply = options.applyEdits()
    if (!apply || busy.value) return
    const id = attempt
    busy.value = true
    try {
      await apply([edit])
      if (id !== attempt) return
      phase.value = next
    } catch (error) {
      if (id === attempt) options.onError(failure(error))
    } finally {
      if (id === attempt) busy.value = false
    }
  }

  /** 撤销：把改好的那一段换回原来的样子，和「还原这处」是同一个动作。 */
  function undo() {
    const done = result.value
    if (done) void swap({ old: done.new, new: done.old }, 'undone')
  }
  /** 撤销之后反悔：再换回改好的样子。 */
  function redo() {
    const done = result.value
    if (done) void swap({ old: done.old, new: done.new }, 'done')
  }
  /** 再改改：在改好的那一段上重新开输入框。 */
  function again() {
    const editor = options.editor()
    const range = editor && target(editor)
    if (range) open(range.from, range.to)
  }

  onScopeDispose(close)

  return { phase, busy, open, send, close, undo, redo, again }
}

export type DocRewriteController = ReturnType<typeof useDocRewrite>
