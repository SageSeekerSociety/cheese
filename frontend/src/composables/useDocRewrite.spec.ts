// @vitest-environment jsdom
// 「让{agent}改」：请求说的是人选中的那几个字；改好的字从协同文档那一路到；撤销把同
// 一处换回去；失败时什么都不留在正文上。
import type { DocRewriteRequest, DocRewriteResult } from '../lib/docEdits'

import { effectScope } from 'vue'
import { Editor } from '@tiptap/core'
import Collaboration from '@tiptap/extension-collaboration'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Y from 'yjs'

import { createEditMarks, editMarks } from '../lib/docEditMarks'
import { flatText, occurrences, rangeOf } from '../lib/docEdits'
import { docExtensions, exportMarkdown, writeMarkdown } from '../lib/docSchema'

import { useDocRewrite } from './useDocRewrite'

import { setLocale, t } from '@/i18n'

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
  rewrite: (request: DocRewriteRequest) => Promise<DocRewriteResult>,
  applyEdits = vi.fn(async () => {})
) {
  const onError = vi.fn()
  const scope = effectScope()
  const ctl = scope.run(() =>
    useDocRewrite({
      editor: () => editor,
      rewrite: () => rewrite,
      applyEdits: () => applyEdits,
      agentName: () => '芝士',
      onError,
    })
  )!
  cleanups.push(() => scope.stop())
  return { ctl, onError, applyEdits }
}

describe('让 AI 队友改选中的字', () => {
  it('发出去的是选中的那几个字，撤销把改好的那一段换回原样', async () => {
    const { doc, editor } = room('第一段。\n\n数据量到一千万行时开始评估迁移。')
    const rewrite = service(doc, '五百万')
    const { ctl, applyEdits } = controller(editor, rewrite)

    const range = find(editor, '一千万')
    ctl.open(range.from, range.to)
    await ctl.send('改成五百万')

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

    const range = find(editor, '一千万')
    ctl.open(range.from, range.to)
    // 另一个人打开同一篇，在这一段前面补了一句。
    const peer = new Y.Doc()
    Y.applyUpdate(peer, Y.encodeStateAsUpdate(doc))
    peer.on('update', (u: Uint8Array) => Y.applyUpdate(doc, u))
    const other = new Editor({ extensions: [...docExtensions(), Collaboration.configure({ document: peer })] })
    cleanups.push(() => other.destroy())
    other.commands.insertContentAt(1, '据测试，')
    await ctl.send('改成五百万')

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

    const range = find(editor, '一千万')
    ctl.open(range.from, range.to)
    await ctl.send('改成五百万')

    expect(onError).toHaveBeenCalledWith('这段已经被改过，重新选一下')
    expect(ctl.phase.value).toBe('idle')
    expect(editMarks(editor.state).target).toBeNull()
  })

  it('选区切开了格式：不发请求，说无法单独修改', async () => {
    const { editor } = room('数据量到**一千万行**时开始评估迁移。')
    const rewrite = vi.fn()
    const { ctl, onError } = controller(editor, rewrite)

    ctl.open(find(editor, '万行').from, find(editor, '时开始').to)
    await ctl.send('改短一点')

    expect(rewrite).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledWith(t('work.room.docEdit.unmappable'))
  })
})
