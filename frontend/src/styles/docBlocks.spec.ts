// A document's status tag ({✓ 通过}) is styled by a global stylesheet, because
// every editor and reader of the document renders it. Its look must reach the
// tags and nothing else: the roster's rows and the admin queue's rows carry a
// `data-status` of their own, and a row dressed as a tag gains padding and a
// leading mark that push its cells out from under their headers.
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { DOMSerializer } from '@tiptap/pm/model'
import { afterEach, beforeEach, expect, it } from 'vitest'

import { parseMarkdown } from '@/lib/docSchema'
import { refChip } from '@/lib/refChip'

// Read from disk: the test config stubs stylesheet imports, `?raw` included.
const docBlocksCss = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'docBlocks.css'), 'utf8')
const styleCss = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../style.css'), 'utf8')

let style: HTMLStyleElement

beforeEach(() => {
  style = document.createElement('style')
  style.textContent = docBlocksCss
  document.head.appendChild(style)
})

afterEach(() => {
  style.remove()
  document.body.innerHTML = ''
})

function renderDoc(md: string): HTMLElement {
  const doc = parseMarkdown(md)
  const host = document.createElement('div')
  host.appendChild(DOMSerializer.fromSchema(doc.type.schema).serializeFragment(doc.content))
  document.body.appendChild(host)
  return host
}

it('a status tag in a document is drawn as a tag', () => {
  const host = renderDoc('这一项 {✓ 通过}')
  const tag = host.querySelector('[data-status]') as HTMLElement

  expect(tag.textContent).toBe('通过')
  expect(getComputedStyle(tag).whiteSpace).toBe('nowrap')
  expect(getComputedStyle(tag).paddingLeft).toBe('6px')
})

it('a table row that records a status is not drawn as a tag', () => {
  document.body.innerHTML =
    '<table><tbody><tr data-status="IN_PROGRESS"><td>第 1 版 · 今天 10:52</td><td>10月20日</td></tr></tbody></table>'
  const row = document.querySelector('tr') as HTMLElement

  expect(getComputedStyle(row).whiteSpace).not.toBe('nowrap')
  expect(getComputedStyle(row).paddingLeft).not.toBe('6px')
})

it('a list row that records a status is not drawn as a tag', () => {
  document.body.innerHTML = '<div role="row" data-status="open"><span role="gridcell">反馈</span></div>'
  const row = document.querySelector('[role="row"]') as HTMLElement

  expect(getComputedStyle(row).paddingLeft).not.toBe('6px')
})

it('正文里的下划线和删除线不躲开字母的下伸部分', () => {
  const appStyle = document.createElement('style')
  appStyle.textContent = styleCss
  document.head.appendChild(appStyle)
  try {
    const host = document.createElement('div')
    // 三套正文各一处：Markdown 阅读、富文本编辑器与只读、实况文档。
    host.innerHTML =
      '<div class="md-reader"><p><s>qqq</s><u>qqq</u></p></div>' +
      '<div class="rt-content"><p><s>qqq</s><u>qqq</u></p></div>' +
      '<div class="doc-prose"><p><s>qqq</s><u>qqq</u></p></div>'
    document.body.appendChild(host)

    for (const el of host.querySelectorAll('u, s')) {
      // 浏览器默认会躲开 g j p q y 的尾巴，每躲一次断一截；正文里关掉它，线是一整条。
      expect(getComputedStyle(el).getPropertyValue('text-decoration-skip-ink')).toBe('none')
    }
  } finally {
    appStyle.remove()
  }
})

it('a focusable reference chip keeps its colour over Vuetify’s later [role=button] { color: inherit } rule', () => {
  const appStyle = document.createElement('style')
  appStyle.textContent = styleCss
  const vuetifyReset = document.createElement('style')
  vuetifyReset.textContent = 'button, [type=button], [role=button] { cursor: pointer; color: inherit; }'
  document.head.append(appStyle, vuetifyReset)
  try {
    const names = { mentionNames: { alice: 'Alice' }, topicTitles: {} }
    const referred = refChip('@', 'alice', names)
    const notified = refChip('@', 'alice', { ...names, notified: true })
    document.body.append(referred, notified)

    const root = getComputedStyle(document.documentElement)
    expect(getComputedStyle(referred).color).toBe(root.getPropertyValue('--accent').trim())
    expect(getComputedStyle(notified).color).toBe(root.getPropertyValue('--accent-on').trim())
  } finally {
    appStyle.remove()
    vuetifyReset.remove()
  }
})
