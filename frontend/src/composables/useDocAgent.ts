// 选中一段字，点 AI 队友：一个输入框，点常用的说法就直接改，自己写一句就是问它。
//
// 改：AI 队友直接改掉，人可以撤销。问：这句话连同选中的字发成一条点了它名的评论，
// 回答回到那条评论下面；回答到了，也在选中的字旁边的小卡上给出来。
//
// 这一层只管这一次走到哪一步，和正文上画什么；请求本身由上面递进来（取数在
// usePanelDoc），所以它和画它的组件都不认识接口。
//
// 改好的字不从回执里取：服务端改的是协同文档，新的字和别人打的字一样从协同那一路到
// 编辑器。回执说的是「改成了什么」，这一层在正文里找到它，亮一下，再把条子放在旁边。
import type { Editor } from '@tiptap/core'
import type { SelectionTarget } from '../lib/docBubble'
import type { EditTarget } from '../lib/docEditMarks'
import type { DocEdit, DocRewriteRequest, DocRewriteResult } from '../lib/docEdits'

import { onScopeDispose, ref, shallowRef } from 'vue'

import { editMarks, nearestText, setEditMarks } from '../lib/docEditMarks'
import { editFailure, plainOf } from '../lib/docEdits'
import { rewriteTarget } from '../lib/docRewrite'

import { t } from '@/i18n'

/** 输入框开着（asking）、改着（pending）、改好了（done）、撤销了（undone）；
 *  问了在等回答（waiting）、回答到了（answered）。 */
export type AgentPhase = 'idle' | 'asking' | 'pending' | 'done' | 'undone' | 'waiting' | 'answered'

export interface DocAgentOptions {
  editor: () => Editor | null | undefined
  /** 让 AI 队友改选中的字；不能改（只读、没有这个动作）时是 undefined。 */
  rewrite: () => ((request: DocRewriteRequest) => Promise<DocRewriteResult>) | undefined
  applyEdits: () => ((edits: DocEdit[]) => Promise<unknown>) | undefined
  /** 把一句话连同选中的字发成点了 AI 队友名的评论；回执是那条评论的 id。 */
  ask: () => ((target: SelectionTarget, question: string) => Promise<string>) | undefined
  /** 那条评论下 AI 队友的回答；还没有时是 null。 */
  answerOf: (threadId: string) => Promise<string | null>
  agentName: () => string
  onError: (message: string) => void
}

/** 改好的字最多等这么久才到；到不了也不再等，条子照样给。 */
const ARRIVAL_MS = 10_000
/** 新的字亮多久。 */
const FLASH_MS = 2400
/** 问了之后隔多久看一次有没有回答，最多看多久。回答再晚也在评论里。 */
const ANSWER_POLL_MS = 3000
const ANSWER_WAIT_MS = 5 * 60_000

export function useDocAgent(options: DocAgentOptions) {
  const phase = ref<AgentPhase>('idle')
  const busy = ref(false)
  const result = shallowRef<DocRewriteResult | null>(null)
  /** 这一段能不能直接改：能时输入框里给常用的说法。 */
  const editable = ref(false)
  /** 问的那条评论，和它下面的回答（等太久没有回答时是 null）。 */
  const threadId = ref<string | null>(null)
  const answer = ref<string | null>(null)
  let selection: SelectionTarget | null = null
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
  function open(from: number, to: number, target: SelectionTarget) {
    const editor = options.editor()
    if (!editor || (!options.rewrite() && !options.ask())) return
    attempt++
    settle()
    result.value = null
    threadId.value = null
    answer.value = null
    selection = target
    editable.value = !!options.rewrite() && !!rewriteTarget(editor.state, from, to)
    phase.value = 'asking'
    mark(editor, { from, to, mode: 'select', label: '' })
  }

  function close() {
    attempt++
    settle()
    phase.value = 'idle'
    result.value = null
    threadId.value = null
    answer.value = null
    selection = null
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

  /** 照一句要求直接改。 */
  async function edit(instruction: string) {
    const editor = options.editor()
    const rewrite = options.rewrite()
    const range = editor && target(editor)
    const text = instruction.trim()
    if (!editor || !rewrite || !range || !text || phase.value !== 'asking') return
    const request = rewriteTarget(editor.state, range.from, range.to)
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
      options.onError(editFailure(error))
    }
  }

  /** 问它：发成评论，在小卡上等它的回答。 */
  async function question(text: string) {
    const ask = options.ask()
    const asked = selection
    const body = text.trim()
    if (!ask || !asked || !body || phase.value !== 'asking') return
    const id = ++attempt
    phase.value = 'waiting'
    try {
      const thread = await ask(asked, body)
      if (id !== attempt) return
      threadId.value = thread
      awaitAnswer(thread, id)
    } catch (error) {
      if (id !== attempt) return
      close()
      options.onError(error instanceof Error && error.message ? error.message : t('work.room.comments.postFailed'))
    }
  }

  function awaitAnswer(thread: string, id: number) {
    const started = Date.now()
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      timer = undefined
      const text = await options.answerOf(thread).catch(() => null)
      if (id !== attempt) return
      if (text) {
        answer.value = text
        phase.value = 'answered'
        return
      }
      if (Date.now() - started >= ANSWER_WAIT_MS) {
        phase.value = 'answered'
        return
      }
      timer = setTimeout(() => void poll(), ANSWER_POLL_MS)
    }
    timer = setTimeout(() => void poll(), ANSWER_POLL_MS)
    stopWaiting = () => {
      if (timer) clearTimeout(timer)
    }
  }

  async function swap(edit: DocEdit, next: AgentPhase) {
    const apply = options.applyEdits()
    if (!apply || busy.value) return
    const id = attempt
    busy.value = true
    try {
      await apply([edit])
      if (id !== attempt) return
      phase.value = next
    } catch (error) {
      if (id === attempt) options.onError(editFailure(error))
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
    if (!editor || !range) return
    open(range.from, range.to, {
      anchorId: selection?.anchorId ?? null,
      quote: editor.state.doc.textBetween(range.from, range.to, ' ').trim(),
    })
  }

  onScopeDispose(close)

  return { phase, busy, editable, threadId, answer, open, edit, question, close, undo, redo, again }
}

export type DocAgentController = ReturnType<typeof useDocAgent>
