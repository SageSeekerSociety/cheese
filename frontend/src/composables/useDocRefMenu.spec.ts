// @vitest-environment jsdom
// 文档里打 @ 挑人、打 # 挑话题：正文里留下的是引用，读的人看到的是名字。
import type { Topic } from '../cx_types'
import type { RefNames } from '../lib/refChip'
import type { MentionPoolEntry } from './useRoomMentionPicker'

import Collaboration from '@tiptap/extension-collaboration'
import { Editor } from '@tiptap/vue-3'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import * as Y from 'yjs'

import { createTokenChips, tokenChipsKey } from '../lib/docDecorations'
import { docExtensions, exportMarkdown, writeMarkdown } from '../lib/docSchema'

import { useDocRefMenu } from './useDocRefMenu'

import { setLocale } from '@/i18n'

const PEOPLE: MentionPoolEntry[] = [
  { handle: 'lixue', label: '李雪', avatar: null, agent: false },
  { handle: 'wangyu', label: '王宇', avatar: null, agent: false },
]
const TOPICS = [{ id: '7f3a9c2e-51b4', title: '接口改版', kind: 'work', status: 'active' }] as unknown as Topic[]

const editors: Editor[] = []
beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  editors.splice(0).forEach((editor) => editor.destroy())
  document.body.replaceChildren()
})

function open(
  md: string,
  names: RefNames = { mentionNames: { lixue: '李雪' }, topicTitles: { '7f3a9c2e-51b4': '接口改版' } }
) {
  const doc = new Y.Doc()
  writeMarkdown(doc, md)
  const menu = useDocRefMenu({ people: () => PEOPLE, topics: () => TOPICS })
  // 菜单量位置用的是编辑区外面那层，和面板里一样。
  const wrap = document.createElement('div')
  wrap.className = 'doc-editor-wrap'
  document.body.append(wrap)
  const shown = { names }
  const editor = new Editor({
    element: wrap,
    extensions: [
      ...docExtensions(),
      Collaboration.configure({ document: doc }),
      createTokenChips({ names: () => shown.names }),
      menu.extension,
    ],
  })
  editors.push(editor)
  return { doc, editor, menu, shown }
}

/** 在文末打字，一个字一个事务，和键盘一样。候选是异步送到菜单的，等它一拍。 */
async function type(editor: Editor, text: string) {
  editor.commands.setTextSelection(editor.state.doc.content.size - 1)
  for (const ch of text) editor.view.dispatch(editor.state.tr.insertText(ch))
  await new Promise((resolve) => setTimeout(resolve))
}

function press(editor: Editor, key: string) {
  editor.view.dom.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
}

describe('打 @ 挑一个人', () => {
  it('正文里留下的是这个人的引用，存成 Markdown 也是', async () => {
    const { doc, editor, menu } = open('负责人\n')
    await type(editor, '@李')
    expect(menu.menu.value?.items.map((i) => i.label)).toEqual(['李雪'])
    menu.pick(menu.menu.value!.items[0])
    expect(exportMarkdown(doc).trim()).toBe('负责人<@lixue>')
    expect(menu.menu.value).toBeNull()
  })

  it('读的人看到的是名字，不是账号', () => {
    const { editor } = open('负责人 <@lixue>\n')
    expect(editor.view.dom.textContent).toContain('@李雪')
  })

  it('名册比正文晚到，到了之后名字换上去', () => {
    const { editor, shown } = open('负责人 <@lixue>\n', { mentionNames: {}, topicTitles: {} })
    expect(editor.view.dom.textContent).not.toContain('李雪')
    shown.names = { mentionNames: { lixue: '李雪' }, topicTitles: {} }
    editor.view.dispatch(editor.state.tr.setMeta(tokenChipsKey, true))
    expect(editor.view.dom.textContent).toContain('@李雪')
  })

  it('紧跟在中文后面也能打开', async () => {
    const { editor, menu } = open('请\n')
    await type(editor, '@王')
    expect(menu.menu.value?.items.map((i) => i.label)).toEqual(['王宇'])
  })

  it('邮箱地址里的 @ 不开菜单', async () => {
    const { editor, menu } = open('写信给 li\n')
    await type(editor, '@w')
    expect(menu.menu.value).toBeNull()
  })

  it('代码块里的 @ 不开菜单', async () => {
    const { editor, menu } = open('```\nx = 1\n```\n')
    await type(editor, ' @李')
    expect(menu.menu.value).toBeNull()
  })
})

describe('打 # 挑一个话题', () => {
  it('正文里留下的是话题的引用', async () => {
    const { doc, editor, menu } = open('详见\n')
    await type(editor, '#接口')
    menu.pick(menu.menu.value!.items[0])
    expect(exportMarkdown(doc).trim()).toBe('详见<#7f3a9c2e-51b4>')
  })

  it('编号不弹出菜单', async () => {
    const { editor, menu } = open('见 issue \n')
    await type(editor, '#2423')
    expect(menu.menu.value).toBeNull()
  })
})

describe('引用当作一个整体', () => {
  it('在它后面按退格，整个引用一起删掉', () => {
    const { doc, editor } = open('负责人 <@lixue>\n')
    editor.commands.setTextSelection(editor.state.doc.content.size - 1)
    press(editor, 'Backspace')
    expect(exportMarkdown(doc).trim()).toBe('负责人')
  })

  it('光标往左一步就越过整个引用', () => {
    const { doc, editor } = open('负责人 <@lixue>\n')
    editor.commands.setTextSelection(editor.state.doc.content.size - 1)
    press(editor, 'ArrowLeft')
    editor.view.dispatch(editor.state.tr.insertText('：'))
    expect(exportMarkdown(doc)).toBe('负责人 ：<@lixue>')
  })
})
