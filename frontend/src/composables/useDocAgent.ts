// 在文档里找 AI 队友：选中一段（或者不选，对整篇）开出输入框，点一个常用的说法或者
// 自己写一句。它在自己的会话里回答；要它改，它直接改正文，改了什么在正文里标出来，
// 可以撤销；只是问，回答在小卡上，可以转成评论。改完还能接着说（「再短一点」），说的
// 是同一个会话。
//
// 这一层只管这一次走到哪一步，和正文上画什么；请求本身由上面递进来（取数在
// usePanelDoc），所以它和画它的组件都不认识接口。
//
// 改好的字不从回执里取：服务端改的是协同文档，新的字和别人打的字一样从协同那一路到
// 编辑器。回执说的是改了哪几处，这一层在正文里找到它们、标出来，再把条子放在旁边。
import type { Editor } from '@tiptap/core'
import type {
  AgentPreset,
  AgentScope,
  AgentSelection,
  DocAgentListener,
  DocAgentRequest,
  PresetContext,
} from '../lib/docAgent'
import type { CommentSpot } from '../lib/docCommentSpots'
import type { DocEdit } from '../lib/docEdits'

import { computed, onScopeDispose, ref, shallowRef } from 'vue'

import { StreamCut } from '../api/eventStream'
import { isChinese } from '../lib/docAgent'
import { anchorComment } from '../lib/docCommentSpots'
import { editMarks, nearestText, setEditMarks } from '../lib/docEditMarks'
import { editFailure, plainOf } from '../lib/docEdits'
import { rewriteTarget } from '../lib/docRewrite'

import { t } from '@/i18n'

/** 输入框开着（asking）、排着队（queued）、在做（working）、改好了（done）、撤销了
 *  （undone）、答了（answered）。 */
export type AgentPhase = 'idle' | 'asking' | 'queued' | 'working' | 'done' | 'undone' | 'answered'

export interface DocAgentOptions {
  editor: () => Editor | null | undefined
  scope: AgentScope
  /** 问一次，答的过程一条条交给 `listener`；问不了时是 undefined。 */
  ask: () => ((request: DocAgentRequest, listener: DocAgentListener) => Promise<void>) | undefined
  stop: () => ((conversation: string) => Promise<unknown>) | undefined
  /** 这篇文档能不能改。 */
  editable: () => boolean
  applyEdits: () => ((edits: DocEdit[]) => Promise<unknown>) | undefined
  /** 把这次的回答放进一条新的评论（评的是选中的那几个字，写着问了什么）；回执是那条评论的 id。 */
  toComment: () => ((conversation: string, quote: string, question: string) => Promise<string>) | undefined
  agentName: () => string
  onError: (message: string) => void
}

/** 改好的字最多等这么久才到；到不了也不再等，条子照样给。 */
const ARRIVAL_MS = 10_000

export function useDocAgent(options: DocAgentOptions) {
  const phase = ref<AgentPhase>('idle')
  /** 这一次是要它改，还是只问。 */
  const kind = ref<'edit' | 'ask'>('ask')
  const answer = ref('')
  /** 回答被人停下了：`answer` 是停下时写出的部分。 */
  const stopped = ref(false)
  const edits = shallowRef<DocEdit[]>([])
  const busy = ref(false)
  /** 输入框里给哪些常用的说法。 */
  const context = ref<PresetContext>({ editable: false, list: false, chinese: true })
  let conversation: string | null = null
  let question = ''
  let selection: CommentSpot | null = null
  // 每开一次、关一次就换一个号：晚到的回执对不上号就不认。
  let attempt = 0
  let abort: AbortController | null = null
  let stopWaiting: (() => void) | null = null

  const editing = computed(() => phase.value === 'queued' || phase.value === 'working')

  function editor() {
    const e = options.editor()
    return e && !e.isDestroyed ? e : null
  }
  function target() {
    const e = editor()
    return e ? editMarks(e.state).target : null
  }
  function paint(patch: Parameters<typeof setEditMarks>[1]) {
    const e = editor()
    if (e) e.view.dispatch(setEditMarks(e.state.tr, patch))
  }
  function settle() {
    stopWaiting?.()
    stopWaiting = null
  }
  /** 回到输入框：选中的那段照旧标着，不再写着「在改」。 */
  function backToBox() {
    phase.value = 'asking'
    const now = target()
    if (now) paint({ target: { ...now, mode: 'select', label: '' } })
  }

  /** 选中的那一段开出输入框；对整篇时不带范围。 */
  function open(range?: CommentSpot) {
    if (!options.ask()) return
    close()
    const e = editor()
    if (options.scope === 'selection') {
      if (!e || !range) return
      selection = range
      const text = e.state.doc.textBetween(range.from, range.to, '\n')
      const $from = e.state.doc.resolve(range.from)
      const top = $from.depth >= 1 ? $from.node(1) : null
      context.value = {
        editable: options.editable() && !!rewriteTarget(e.state, range.from, range.to),
        list: !!top && /list/i.test(top.type.name),
        chinese: isChinese(text),
        code: $from.parent.type.name === 'codeBlock',
      }
      paint({ target: { from: range.from, to: range.to, mode: 'select', label: '' }, review: null })
    } else {
      context.value = { editable: options.editable(), list: false, chinese: true }
    }
    phase.value = 'asking'
  }

  function close() {
    attempt++
    settle()
    abort?.abort()
    abort = null
    phase.value = 'idle'
    answer.value = ''
    stopped.value = false
    edits.value = []
    busy.value = false
    conversation = null
    selection = null
    if (target() || (editor() && editMarks(editor()!.state).review)) paint({ target: null, review: null })
  }

  /** 选中的那段，按服务端读得懂的样子写出来。 */
  function selected(): AgentSelection | undefined {
    if (options.scope !== 'selection') return undefined
    const e = editor()
    const range = target()
    if (!e || !range) return undefined
    const exact = rewriteTarget(e.state, range.from, range.to)
    if (exact) return { block: exact.block, start: exact.start, end: exact.end }
    const text = e.state.doc.textBetween(range.from, range.to, '\n')
    return { block: text, start: 0, end: text.length }
  }

  /** 在正文里找到改好的那几处：标出来，条子贴到第一处旁边。 */
  function land(id: number, changed: DocEdit[]): boolean {
    const e = editor()
    if (!e || id !== attempt) return true
    const near = target()?.from ?? 0
    const first = changed[0] && nearestText(e.state.doc, plainOf(changed[0].new), near)
    if (!first) return false
    paint({ target: { ...first, mode: 'anchor', label: '' }, review: { edits: changed, active: null } })
    return true
  }

  function arrive(id: number, changed: DocEdit[]) {
    settle()
    if (land(id, changed)) return
    const e = editor()
    if (!e) return
    const onChange = ({ transaction }: { transaction: { docChanged: boolean } }) => {
      if (transaction.docChanged && land(id, changed)) settle()
    }
    const timer = setTimeout(settle, ARRIVAL_MS)
    e.on('transaction', onChange)
    stopWaiting = () => {
      e.off('transaction', onChange)
      clearTimeout(timer)
    }
  }

  async function send(request: DocAgentRequest, asked: 'edit' | 'ask', label: string) {
    const ask = options.ask()
    if (!ask) return
    const id = ++attempt
    settle()
    abort?.abort()
    const controller = new AbortController()
    abort = controller
    kind.value = asked
    question = label
    answer.value = ''
    stopped.value = false
    phase.value = 'working'
    const range = target()
    if (range && asked === 'edit')
      paint({
        target: { ...range, mode: 'pending', label: t('work.room.docAgent.working', { agent: options.agentName() }) },
      })
    const mine = () => id === attempt
    const listener: DocAgentListener = {
      conversation: (value) => mine() && (conversation = value),
      queued: () => mine() && (phase.value = 'queued'),
      working: () => mine() && (phase.value = 'working'),
      delta: (text, at) => mine() && (answer.value = answer.value.slice(0, at ?? answer.value.length) + text),
      done: (result) => {
        if (!mine()) return
        edits.value = result.edits
        if (result.edits.length) {
          phase.value = 'done'
          arrive(id, result.edits)
        } else if (result.stopped && !result.answer) {
          backToBox()
        } else {
          answer.value = result.answer
          stopped.value = result.stopped
          phase.value = 'answered'
          const now = target()
          if (now) paint({ target: { ...now, mode: 'select', label: '' } })
        }
      },
      error: (message) => {
        if (!mine()) return
        options.onError(message || t('work.room.docAgent.failed', { agent: options.agentName() }))
        backToBox()
      },
    }
    try {
      // 接着说的进同一个会话，它记得选中的是哪段；改过之后这里的范围只剩改了的第一处，
      // 不再拿它当选中的字。
      const followUp = conversation ? { conversation } : { selection: selected() }
      await ask({ ...request, ...followUp }, listener)
    } catch (error) {
      if (!mine() || controller.signal.aborted) return
      options.onError(
        error instanceof StreamCut ? t('work.room.docAgent.failed', { agent: options.agentName() }) : editFailure(error)
      )
      backToBox()
    } finally {
      // 读不到结果就回到输入框。改了什么，正文会自己到。
      if (mine() && editing.value) backToBox()
    }
  }

  /** 点一个常用的说法。 */
  function run(preset: AgentPreset, label: string) {
    if (phase.value !== 'asking') return
    void send({ preset: preset.id }, preset.kind, label)
  }

  /** 自己写的一句：改还是答由它判断。 */
  function say(text: string) {
    const body = text.trim()
    if (!body || (phase.value !== 'asking' && phase.value !== 'done' && phase.value !== 'answered')) return
    void send({ text: body }, context.value.editable ? 'edit' : 'ask', body)
  }

  async function stop() {
    const halt = options.stop()
    if (!editing.value) return
    if (conversation && halt) {
      await halt(conversation).catch(() => undefined)
    } else {
      abort?.abort()
      backToBox()
    }
  }

  async function swap(next: 'done' | 'undone') {
    const apply = options.applyEdits()
    const changed = edits.value
    if (!apply || busy.value || !changed.length) return
    const id = attempt
    busy.value = true
    const batch = next === 'undone' ? [...changed].reverse().map((e) => ({ old: e.new, new: e.old })) : changed
    try {
      await apply(batch)
      if (id !== attempt) return
      phase.value = next
      if (next === 'undone') paint({ review: null })
      else arrive(id, changed)
    } catch (error) {
      if (id === attempt) options.onError(editFailure(error))
    } finally {
      if (id === attempt) busy.value = false
    }
  }

  /** 撤销：把改了的几处都换回原来的样子。 */
  const undo = () => swap('undone')
  /** 撤销之后反悔：再改回去。 */
  const redo = () => swap('done')

  /** 把这次的回答放进一条新评论；回执是那条评论的 id。 */
  async function toComment(): Promise<string | null> {
    const post = options.toComment()
    if (!post || !conversation || phase.value !== 'answered') return null
    const at = selection
    try {
      const thread = await post(conversation, at?.quote ?? '', question)
      const e = editor()
      if (e && at) anchorComment(e, at, thread)
      close()
      return thread
    } catch (error) {
      options.onError(editFailure(error))
      return null
    }
  }

  onScopeDispose(close)

  return {
    phase,
    kind,
    answer,
    edits,
    busy,
    context,
    editing,
    open,
    close,
    run,
    say,
    stop,
    stopped,
    undo,
    redo,
    toComment,
  }
}

export type DocAgentController = ReturnType<typeof useDocAgent>
