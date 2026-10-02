// @vitest-environment jsdom
// 选中文字点 AI 队友：改的请求说的是人选中的那几个字；改好的字从协同文档那一路到；撤销
// 把同一处换回去；失败时什么都不留在正文上。问的话连同选中的字发出去，回答到了就给出来。
import type { DocRewriteRequest, DocRewriteResult } from '../lib/docEdits'

import { effectScope } from 'vue'
import { Editor } from '@tiptap/core'
import Collaboration from '@tiptap/extension-collaboration'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Y from 'yjs'

import { createEditMarks, editMarks } from '../lib/docEditMarks'
import { flatText, occurrences, rangeOf } from '../lib/docEdits'
import { docExtensions, exportMarkdown, writeMarkdown } from '../lib/docSchema'

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

/** The service: replace the range in the block, write the document, answer with both blocks. */
function service(doc: Y.Doc, replacement: string) {
  return vi.fn(async (request: DocRewriteRequest): Promise<DocRewriteResult> => {
    const next = request.block.slice(0, request.start) + replacement + request.block.slice(request.end)
    writeMarkdown(doc, exportMarkdown(doc).replace(request.block, next))
    return { old: request.block, new: next, replacement }
  })
}

function controller(
  editor: Editor,
  rewrite: ((request: DocRewriteRequest) => Promise<DocRewriteResult>) | undefined,
  {
    applyEdits = vi.fn(async () => {}),
    ask = vi.fn(async () => 'thread-1'),
    answerOf = vi.fn(async (): Promise<string | null> => null),
  } = {}
) {
  const onError = vi.fn()
  const scope = effectScope()
  const ctl = scope.run(() =>
    useDocAgent({
      editor: () => editor,
      rewrite: () => rewrite,
      applyEdits: () => applyEdits,
      ask: () => ask,
      answerOf,
      agentName: () => '芝士',
      onError,
    })
  )!
  cleanups.push(() => scope.stop())
  return { ctl, onError, applyEdits, ask, answerOf }
}

/** 选中一段：开输入框要的那三样。 */
function select(editor: Editor, text: string) {
  const range = find(editor, text)
  return [range.from, range.to, { anchorId: 'node-1', quote: text }] as const
}

describe('让 AI 队友改选中的字', () => {
  it('发出去的是选中的那几个字，撤销把改好的那一段换回原样', async () => {
    const { doc, editor } = room('第一段。\n\n数据量到一千万行时开始评估迁移。')
    const rewrite = service(doc, '五百万')
    const { ctl, applyEdits } = controller(editor, rewrite)

    ctl.open(...select(editor, '一千万'))
    await ctl.edit('改成五百万')

    const [request] = rewrite.mock.calls[0]
    expect(request.block.slice(request.start, request.end)).toBe('一千万')
    expect(request.instruction).toBe('改成五百万')
    expect(exportMarkdown(doc)).toContain('数据量到五百万行')
    expect(ctl.phase.value).toBe('done')

    ctl.undo()
    await vi.waitFor(() => expect(ctl.phase.value).toBe('undone'))
    expect(applyEdits).toHaveBeenCalledWith([
      { old: '数据量到五百万行时开始评估迁移。', new: '数据量到一千万行时开始评估迁移。' },
    ])
  })

  it('别人在前面打了字，发出去的仍是自己选中的那几个字', async () => {
    const { doc, editor } = room('数据量到一千万行时开始评估迁移。')
    const rewrite = service(doc, '五百万')
    const { ctl } = controller(editor, rewrite)

    ctl.open(...select(editor, '一千万'))
    // 另一个人打开同一篇，在这一段前面补了一句。
    const peer = new Y.Doc()
    Y.applyUpdate(peer, Y.encodeStateAsUpdate(doc))
    peer.on('update', (u: Uint8Array) => Y.applyUpdate(doc, u))
    const other = new Editor({ extensions: [...docExtensions(), Collaboration.configure({ document: peer })] })
    cleanups.push(() => other.destroy())
    other.commands.insertContentAt(1, '据测试，')
    await ctl.edit('改成五百万')

    const [request] = rewrite.mock.calls[0]
    expect(request.block.slice(request.start, request.end)).toBe('一千万')
    expect(exportMarkdown(doc)).toBe('据测试，数据量到五百万行时开始评估迁移。')
  })

  it('这段已经被改过：照服务端的话说，正文上不留「修改中」', async () => {
    const { editor } = room('数据量到一千万行时开始评估迁移。')
    const stale = Object.assign(new Error('这段已经被改过，重新选一下'), { status: 409 })
    const { ctl, onError } = controller(
      editor,
      vi.fn(async () => Promise.reject(stale))
    )

    ctl.open(...select(editor, '一千万'))
    await ctl.edit('改成五百万')

    expect(onError).toHaveBeenCalledWith('这段已经被改过，重新选一下')
    expect(ctl.phase.value).toBe('idle')
    expect(editMarks(editor.state).target).toBeNull()
  })

  it('选区切开了格式：不能直接改，只能问', async () => {
    const { editor } = room('数据量到**一千万行**时开始评估迁移。')
    const rewrite = vi.fn()
    const { ctl } = controller(editor, rewrite)

    ctl.open(find(editor, '万行').from, find(editor, '时开始').to, { anchorId: null, quote: '万行时开始' })
    expect(ctl.editable.value).toBe(false)
    await ctl.edit('改短一点')

    expect(rewrite).not.toHaveBeenCalled()
  })

  it('不能改这篇文档（只读）：不能直接改，只能问', () => {
    const { editor } = room('数据量到一千万行时开始评估迁移。')
    const { ctl } = controller(editor, undefined)

    ctl.open(...select(editor, '一千万'))
    expect(ctl.phase.value).toBe('asking')
    expect(ctl.editable.value).toBe(false)
  })
})

describe('问 AI 队友', () => {
  it('问题连同选中的字发出去，回答到了就给出来，正文一个字不动', async () => {
    vi.useFakeTimers()
    cleanups.push(() => vi.useRealTimers())
    const { doc, editor } = room('数据量到一千万行时开始评估迁移。')
    const answers: (string | null)[] = [null, '课程要求写的是五百万行。']
    const { ctl, ask } = controller(editor, service(doc, '五百万'), {
      answerOf: vi.fn(async () => answers.shift() ?? null),
    })

    ctl.open(...select(editor, '一千万'))
    await ctl.question('这个数是哪来的？')

    expect(ask).toHaveBeenCalledWith({ anchorId: 'node-1', quote: '一千万' }, '这个数是哪来的？')
    expect(ctl.phase.value).toBe('waiting')
    expect(ctl.threadId.value).toBe('thread-1')

    await vi.advanceTimersByTimeAsync(3000)
    expect(ctl.phase.value).toBe('waiting')
    await vi.advanceTimersByTimeAsync(3000)
    expect(ctl.phase.value).toBe('answered')
    expect(ctl.answer.value).toBe('课程要求写的是五百万行。')
    expect(exportMarkdown(doc)).toBe('数据量到一千万行时开始评估迁移。')
  })

  it('收起之后晚到的回答不再弹出来', async () => {
    vi.useFakeTimers()
    cleanups.push(() => vi.useRealTimers())
    const { editor } = room('数据量到一千万行时开始评估迁移。')
    const { ctl } = controller(editor, undefined, { answerOf: vi.fn(async () => '晚到的回答') })

    ctl.open(...select(editor, '一千万'))
    await ctl.question('这个数是哪来的？')
    ctl.close()
    await vi.advanceTimersByTimeAsync(6000)

    expect(ctl.phase.value).toBe('idle')
    expect(ctl.answer.value).toBeNull()
  })

  it('问题没发出去：说出来，不留小卡', async () => {
    const { editor } = room('数据量到一千万行时开始评估迁移。')
    const { ctl, onError } = controller(editor, undefined, {
      ask: vi.fn(async () => Promise.reject(new Error('评论发送失败'))),
    })

    ctl.open(...select(editor, '一千万'))
    await ctl.question('这个数是哪来的？')

    expect(onError).toHaveBeenCalledWith('评论发送失败')
    expect(ctl.phase.value).toBe('idle')
    expect(editMarks(editor.state).target).toBeNull()
  })
})
