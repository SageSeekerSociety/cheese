// @vitest-environment jsdom
// 在文档里找 AI 队友：问的是人选中的那几个字；常用的说法按编号发，不按名字；它改了什么
// 在正文里标出来，撤销把每一处换回去；接着说的是同一个会话；只是问时正文不动；失败时
// 什么都不留在正文上；关掉以后晚到的回答不认。
import type { AgentPreset, DocAgentListener, DocAgentRequest } from '../lib/docAgent'

import { effectScope } from 'vue'
import { Editor } from '@tiptap/core'
import Collaboration from '@tiptap/extension-collaboration'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Y from 'yjs'

import { spotAt } from '../lib/docCommentSpots'
import { createEditMarks, editMarks } from '../lib/docEditMarks'
import { flatText, occurrences, rangeOf } from '../lib/docEdits'
import { commentAnchors, docExtensions, exportMarkdown, writeMarkdown } from '../lib/docSchema'

import { useDocAgent } from './useDocAgent'

import { setLocale } from '@/i18n'

const cleanups: (() => void)[] = []
beforeEach(() => setLocale('zh-CN'))
afterEach(() => cleanups.splice(0).forEach((fn) => fn()))

function room(markdown: string) {
  const doc = new Y.Doc()
  writeMarkdown(doc, markdown)
  const editor = new Editor({
    extensions: [...docExtensions(), Collaboration.configure({ document: doc }), createEditMarks()],
  })
  cleanups.push(() => editor.destroy())
  return { doc, editor }
}

function find(editor: Editor, text: string) {
  const flat = flatText(editor.state.doc)
  const [at] = occurrences(flat.text, text)
  expect(at).toBeGreaterThanOrEqual(0)
  return rangeOf(flat, at, at + text.length)
}

/** 选中一段：开输入框要的那几样。 */
function select(editor: Editor, text: string) {
  const range = find(editor, text)
  return spotAt(editor, range.from, range.to)
}

/** The service: answers with `answer` after making `edits` in the shared document. */
function service(doc: Y.Doc, { edits = [] as { old: string; new: string }[], answer = '' } = {}) {
  return vi.fn(async (request: DocAgentRequest, listener: DocAgentListener) => {
    listener.conversation('conversation-1')
    listener.working()
    let markdown = exportMarkdown(doc)
    for (const edit of edits) markdown = markdown.replace(edit.old, edit.new)
    if (edits.length) writeMarkdown(doc, markdown)
    listener.done({ answer, edits, stopped: false })
    void request
  })
}

function controller(
  editor: Editor,
  ask: (request: DocAgentRequest, listener: DocAgentListener) => Promise<void>,
  { applyEdits = vi.fn(async () => {}), editable = true, toComment = vi.fn(async () => 'thread-1') } = {}
) {
  const onError = vi.fn()
  const stop = vi.fn(async () => {})
  const scope = effectScope()
  const ctl = scope.run(() =>
    useDocAgent({
      editor: () => editor,
      scope: 'selection',
      ask: () => ask,
      stop: () => stop,
      editable: () => editable,
      applyEdits: () => applyEdits,
      toComment: () => toComment,
      agentName: () => '芝士',
      onError,
    })
  )!
  cleanups.push(() => scope.stop())
  return { ctl, onError, applyEdits, stop, toComment }
}

const polish: AgentPreset = { id: 'polish', kind: 'edit', label: '', icon: '' }
const check: AgentPreset = { id: 'check', kind: 'ask', label: '', icon: '' }

describe('在文档里找 AI 队友', () => {
  it('常用的说法按编号发，问的是选中的那几个字', async () => {
    const { doc, editor } = room('第一段。\n\n数据量到一千万行时开始评估迁移。')
    const ask = service(doc)
    const { ctl } = controller(editor, ask)

    ctl.open(select(editor, '一千万'))
    ctl.run(polish, '润色')
    await vi.waitFor(() => expect(ask).toHaveBeenCalled())

    const [request] = ask.mock.calls[0]
    expect(request.preset).toBe('polish')
    expect(request.text).toBeUndefined()
    const sel = request.selection!
    expect(sel.block.slice(sel.start, sel.end)).toBe('一千万')
  })

  it('改了的几处在正文里标出来；撤销把每一处都换回去，标记也去掉', async () => {
    const { doc, editor } = room('数据量到一千万行时开始评估迁移。\n\n周二例会。')
    const edits = [
      { old: '一千万', new: '五百万' },
      { old: '周二', new: '周三' },
    ]
    const { ctl, applyEdits } = controller(editor, service(doc, { edits }))

    ctl.open(select(editor, '一千万'))
    ctl.run(polish, '润色')
    await vi.waitFor(() => expect(ctl.phase.value).toBe('done'))
    expect(exportMarkdown(doc)).toContain('五百万')
    expect(editMarks(editor.state).review?.edits).toEqual(edits)

    await ctl.undo()
    expect(applyEdits).toHaveBeenCalledWith([
      { old: '周三', new: '周二' },
      { old: '五百万', new: '一千万' },
    ])
    expect(ctl.phase.value).toBe('undone')
    expect(editMarks(editor.state).review).toBeNull()
  })

  it('接着说的是同一个会话，不再拿改过的那一处当选中的字', async () => {
    const { doc, editor } = room('数据量到一千万行时开始评估迁移。\n\n周二例会。')
    const ask = service(doc, {
      edits: [
        { old: '一千万', new: '五百万' },
        { old: '周二', new: '周三' },
      ],
    })
    const { ctl } = controller(editor, ask)

    ctl.open(select(editor, '一千万'))
    ctl.run(polish, '润色')
    await vi.waitFor(() => expect(ctl.phase.value).toBe('done'))
    ctl.say('再短一点')
    await vi.waitFor(() => expect(ask).toHaveBeenCalledTimes(2))

    expect(ask.mock.calls[0][0].conversation).toBeUndefined()
    expect(ask.mock.calls[1][0]).toMatchObject({ conversation: 'conversation-1', text: '再短一点' })
    expect(ask.mock.calls[1][0].selection).toBeUndefined()
  })

  it('只是问：正文不动，回答给出来，能转成评论，评论标在选中的字上', async () => {
    const { doc, editor } = room('数据量到一千万行时开始评估迁移。')
    const before = exportMarkdown(doc)
    const { ctl, toComment } = controller(editor, service(doc, { answer: '一千万和章程里写的一致。' }))

    ctl.open(select(editor, '一千万'))
    ctl.run(check, '检查')
    await vi.waitFor(() => expect(ctl.phase.value).toBe('answered'))

    expect(ctl.answer.value).toBe('一千万和章程里写的一致。')
    expect(exportMarkdown(doc)).toBe(before)
    expect(await ctl.toComment()).toBe('thread-1')
    expect(toComment).toHaveBeenCalledWith('conversation-1', '一千万', '检查')
    const [marked] = commentAnchors(editor.state.doc).get('thread-1') ?? []
    expect(marked && editor.state.doc.textBetween(marked.from, marked.to)).toBe('一千万')
  })

  it('排队时能取消，取消的是这一问', async () => {
    const { editor } = room('数据量到一千万行时开始评估迁移。')
    const ask = vi.fn(async (_request: DocAgentRequest, listener: DocAgentListener) => {
      listener.conversation('conversation-1')
      listener.queued()
      await new Promise(() => {})
    })
    const { ctl, stop } = controller(editor, ask)

    ctl.open(select(editor, '一千万'))
    ctl.run(polish, '润色')
    await vi.waitFor(() => expect(ctl.phase.value).toBe('queued'))
    await ctl.stop()

    expect(stop).toHaveBeenCalledWith('conversation-1')
  })

  it('失败时说出来，正文上不留标记，输入框还在', async () => {
    const { editor } = room('数据量到一千万行时开始评估迁移。')
    const ask = vi.fn(async (_request: DocAgentRequest, listener: DocAgentListener) => {
      listener.error('芝士暂时无法回答，稍后重试')
    })
    const { ctl, onError } = controller(editor, ask)

    ctl.open(select(editor, '一千万'))
    ctl.run(polish, '润色')
    await vi.waitFor(() => expect(onError).toHaveBeenCalledWith('芝士暂时无法回答，稍后重试'))

    expect(ctl.phase.value).toBe('asking')
    expect(editMarks(editor.state).review).toBeNull()
  })

  it('断了没有结果：回到输入框，选中的那段不再写着在改', async () => {
    const { editor } = room('数据量到一千万行时开始评估迁移。')
    const ask = vi.fn(async (_request: DocAgentRequest, listener: DocAgentListener) => {
      listener.conversation('conversation-1')
      listener.working()
    })
    const { ctl } = controller(editor, ask)

    ctl.open(select(editor, '一千万'))
    ctl.run(polish, '润色')
    await vi.waitFor(() => expect(ask).toHaveBeenCalled())
    await Promise.resolve()

    expect(ctl.phase.value).toBe('asking')
    expect(editMarks(editor.state).target?.mode).toBe('select')
  })

  it('关掉以后晚到的回答不认', async () => {
    const { editor } = room('数据量到一千万行时开始评估迁移。')
    let finish: (() => void) | null = null
    const ask = vi.fn(async (_request: DocAgentRequest, listener: DocAgentListener) => {
      await new Promise<void>((resolve) => (finish = resolve))
      listener.done({ answer: '晚到的', edits: [], stopped: false })
    })
    const { ctl } = controller(editor, ask)

    ctl.open(select(editor, '一千万'))
    ctl.run(check, '检查')
    ctl.close()
    finish!()
    await Promise.resolve()

    expect(ctl.phase.value).toBe('idle')
    expect(ctl.answer.value).toBe('')
  })

  it('不能改的文档：自己写的一句只当作问', async () => {
    const { doc, editor } = room('数据量到一千万行时开始评估迁移。')
    const { ctl } = controller(editor, service(doc), { editable: false })

    ctl.open(select(editor, '一千万'))
    expect(ctl.context.value.editable).toBe(false)
    ctl.say('这个数对吗')
    await vi.waitFor(() => expect(ctl.phase.value).toBe('answered'))
    expect(ctl.kind.value).toBe('ask')
  })
})
