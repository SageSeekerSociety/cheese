// @vitest-environment jsdom
//
// 题目详情、知识库、模板里已经存着的正文，是上一台编辑器（vuetify-pro-tiptap）写出的
// JSON；公告存的是它写出的 HTML。换了编辑器以后，这些老内容打开、改一笔、再存回去，
// 原来的东西一样都不能少。
//
// 下面两份样本照那台编辑器当时的配置写：它开着的每一样格式（颜色、字体、字号、高亮、
// 上下标、对齐、表格、代码块、任务列表、附件图……）都各出现一次，属性也照它的写法
// （不带单位的字号、每个段落都带 `textAlign`）。
import type { JSONContent } from '@tiptap/core'

import { createApp, h, nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ATTACHMENT_IMAGE_SOURCE } from './attachmentImageSource'
import TipTapEditor from './TipTapEditor.vue'
import TipTapViewer from './TipTapViewer.vue'

import i18n from '@/i18n'

const images = {
  url: (id: number) => Promise.resolve(`https://files.test/${id}.png`),
  upload: vi.fn(),
}

const text = (value: string, marks?: JSONContent['marks']): JSONContent =>
  marks ? { type: 'text', text: value, marks } : { type: 'text', text: value }
const para = (content: JSONContent[], textAlign: string | null = null): JSONContent => ({
  type: 'paragraph',
  attrs: { textAlign },
  content,
})

const LEGACY_DOC: JSONContent = {
  type: 'doc',
  content: [
    { type: 'heading', attrs: { textAlign: 'center', level: 2 }, content: [text('实验二：内存分配器')] },
    para(
      [
        text('普通、'),
        text('粗体', [{ type: 'bold' }]),
        text('斜体', [{ type: 'italic' }]),
        text('下划线', [{ type: 'underline' }]),
        text('删除线', [{ type: 'strike' }]),
        text('malloc', [{ type: 'code' }]),
        text('红字', [{ type: 'textStyle', attrs: { color: '#F44336', fontFamily: null, fontSize: null } }]),
        text('大号宋体', [{ type: 'textStyle', attrs: { color: null, fontFamily: 'SimSun', fontSize: '18' } }]),
        text('高亮', [{ type: 'highlight', attrs: { color: '#FFEB3B' } }]),
        text('2', [{ type: 'superscript' }]),
        text('i', [{ type: 'subscript' }]),
        text('链接', [
          {
            type: 'link',
            attrs: {
              href: 'https://example.com/lab2',
              target: '_blank',
              rel: 'noopener noreferrer nofollow',
              class: null,
            },
          },
        ]),
        { type: 'hardBreak' },
        text('换行之后'),
      ],
      'justify'
    ),
    {
      type: 'bulletList',
      content: [{ type: 'listItem', content: [para([text('第一条')])] }],
    },
    {
      type: 'orderedList',
      attrs: { start: 3 },
      content: [{ type: 'listItem', content: [para([text('从三开始')])] }],
    },
    {
      type: 'taskList',
      content: [
        { type: 'taskItem', attrs: { checked: true }, content: [para([text('已完成')])] },
        { type: 'taskItem', attrs: { checked: false }, content: [para([text('未完成')])] },
      ],
    },
    { type: 'blockquote', content: [para([text('引用一段')])] },
    { type: 'codeBlock', attrs: { language: 'python' }, content: [text('print("hi")\n\tindent')] },
    { type: 'horizontalRule' },
    {
      type: 'table',
      content: [
        {
          type: 'tableRow',
          content: [
            {
              type: 'tableHeader',
              attrs: { colspan: 1, rowspan: 1, colwidth: [120] },
              content: [para([text('表头')])],
            },
            { type: 'tableHeader', attrs: { colspan: 1, rowspan: 1, colwidth: null }, content: [para([text('说明')])] },
          ],
        },
        {
          type: 'tableRow',
          content: [
            {
              type: 'tableCell',
              attrs: { colspan: 2, rowspan: 1, colwidth: null },
              content: [para([text('合并的格子')])],
            },
          ],
        },
      ],
    },
    {
      type: 'attachmentImage',
      attrs: { attachmentId: 42, alt: null, title: null, width: 1280, height: 720 },
      content: [text('图 1：堆的布局')],
    },
    { type: 'attachmentImage', attrs: { attachmentId: 43, alt: null, title: null, width: 640, height: 480 } },
    para([text('最后一段')], 'right'),
  ],
}

// 同一台编辑器存公告时写的 HTML（它的 renderHTML 原样拼出来的形状）。
const LEGACY_HTML = [
  '<h2 style="text-align: center">公告标题</h2>',
  '<p style="text-align: right">靠右 <span style="color: #F44336; font-size: 18px; font-family: SimSun">红色大字</span>',
  '<mark data-color="#FFEB3B" style="background-color: #FFEB3B; color: inherit">高亮</mark>',
  'H<sub>2</sub>O x<sup>2</sup> <u>下划线</u></p>',
  '<ul data-type="taskList"><li data-checked="true" data-type="taskItem"><label><input type="checkbox" checked><span></span></label><div><p>已报名</p></div></li></ul>',
  '<pre><code class="language-python">print(1)</code></pre>',
  '<table><tbody><tr><th colspan="1" rowspan="1"><p>时间</p></th></tr><tr><td colspan="1" rowspan="1"><p>周五</p></td></tr></tbody></table>',
  '<attachment-img attachmentid="42" width="1280" height="720">报名二维码</attachment-img>',
].join('')

/** 只比内容：没设的属性（null）两边写不写都一样，同一段字上几个标记的先后也不算数。 */
function canonical(node: JSONContent): JSONContent {
  const out: JSONContent = { type: node.type }
  const attrs = Object.fromEntries(Object.entries(node.attrs ?? {}).filter(([, v]) => v !== null && v !== undefined))
  if (Object.keys(attrs).length) out.attrs = attrs
  if (node.text !== undefined) out.text = node.text
  if (node.marks?.length) {
    out.marks = node.marks
      .map((mark) => canonical(mark) as { type: string })
      .sort((a, b) => a.type.localeCompare(b.type))
  }
  if (node.content?.length) out.content = node.content.map(canonical)
  return out
}

interface Mounted {
  host: HTMLElement
  emitted: () => unknown
  editor: () => {
    state: { doc: { content: { size: number } } }
    chain: () => { insertContentAt: (pos: number, content: JSONContent) => { run: () => boolean } }
  }
  unmount: () => void
}

const mounted: Mounted[] = []
afterEach(() => mounted.splice(0).forEach((m) => m.unmount()))

async function mountEditor(value: JSONContent | string, output: 'json' | 'html'): Promise<Mounted> {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const model = ref<unknown>(value)
  const editorRef = ref<{ editor: ReturnType<Mounted['editor']> } | null>(null)
  const app = createApp({
    render: () =>
      h(TipTapEditor, {
        ref: editorRef,
        output,
        modelValue: model.value as JSONContent | string,
        'onUpdate:modelValue': (next: unknown) => {
          model.value = next
        },
      }),
  })
  app.use(i18n)
  app.use(createVuetify({ components, directives }))
  app.provide(ATTACHMENT_IMAGE_SOURCE, images)
  app.mount(host)
  await waitFor(() => expect(editorRef.value?.editor).toBeTruthy())
  const result: Mounted = {
    host,
    emitted: () => model.value,
    editor: () => editorRef.value!.editor,
    unmount: () => {
      app.unmount()
      host.remove()
    },
  }
  mounted.push(result)
  return result
}

/** 在文末补一段 —— 「改一笔」。 */
function appendParagraph(m: Mounted, words: string) {
  const editor = m.editor()
  editor
    .chain()
    .insertContentAt(editor.state.doc.content.size, { type: 'paragraph', content: [{ type: 'text', text: words }] })
    .run()
}

describe('上一台编辑器存下的内容', () => {
  it('JSON 正文打开、改一笔、存回去，原来的每一样都还在', async () => {
    const m = await mountEditor(LEGACY_DOC, 'json')
    appendParagraph(m, '新加的一句')
    await nextTick()

    const saved = canonical(m.emitted() as JSONContent)
    const original = canonical(LEGACY_DOC)
    expect(saved.content!.slice(0, -1)).toEqual(original.content)
    expect(saved.content!.at(-1)).toEqual(canonical(para([text('新加的一句')])))
  })

  it('HTML 公告打开、改一笔、存回去，格式与图都还在', async () => {
    const m = await mountEditor(LEGACY_HTML, 'html')
    appendParagraph(m, '补充说明')
    await nextTick()

    const saved = m.emitted() as string
    const doc = new DOMParser().parseFromString(saved, 'text/html')
    expect(doc.querySelector('h2')?.style.textAlign).toBe('center')
    expect(doc.querySelector('p')?.style.textAlign).toBe('right')
    const styled = Array.from(doc.querySelectorAll('span')).find((s) => s.textContent === '红色大字')!
    expect(styled.style.color).not.toBe('')
    expect(styled.style.fontSize).toBe('18px')
    expect(styled.style.fontFamily).toBe('SimSun')
    expect(doc.querySelector('mark')?.textContent).toBe('高亮')
    expect(doc.querySelector('mark')?.getAttribute('data-color')).toBe('#FFEB3B')
    expect(doc.querySelector('sub')?.textContent).toBe('2')
    expect(doc.querySelector('sup')?.textContent).toBe('2')
    expect(doc.querySelector('u')?.textContent).toBe('下划线')
    expect(doc.querySelector('li[data-type="taskItem"]')?.getAttribute('data-checked')).toBe('true')
    expect(doc.querySelector('pre code')?.className).toContain('language-python')
    expect(doc.querySelector('th')?.textContent).toBe('时间')
    expect(doc.querySelector('td')?.textContent).toBe('周五')
    const image = doc.querySelector('attachment-img')
    expect(image?.getAttribute('attachmentid')).toBe('42')
    expect(image?.textContent).toBe('报名二维码')
    expect(doc.body.textContent).toContain('补充说明')
  })

  it('不带单位的老字号照原来的大小显示', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp({
      render: () =>
        h(TipTapViewer, {
          value: {
            type: 'doc',
            content: [para([text('十八号字', [{ type: 'textStyle', attrs: { fontSize: '18' } }])])],
          },
        }),
    })
    app.use(i18n)
    app.mount(host)
    try {
      await waitFor(() => {
        const span = Array.from(host.querySelectorAll('span')).find((s) => s.textContent === '十八号字')
        expect(span?.style.fontSize).toBe('18px')
      })
    } finally {
      app.unmount()
      host.remove()
    }
  })
})
