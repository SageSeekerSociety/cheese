/**
 * 题目详情这类富文本里，用工具栏设上的格式（字号、字体、颜色、高亮、对齐、上下标、
 * 链接）存下来再打开，还是原样。这几处存 JSON 或 HTML，格式写得进去；存不下的只有实况
 * 文档（Markdown），所以这些按钮只在这里。
 */
import type { JSONContent } from '@tiptap/core'

import { createApp, h, nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import TipTapEditor from './TipTapEditor.vue'
import TipTapViewer from './TipTapViewer.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => {
  setLocale('zh-CN')
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  // 弹层定位要读视口与像素比；happy-dom 里没有。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

interface LiveEditor {
  commands: { setTextSelection: (range: { from: number; to: number }) => boolean }
}

const cleanups: (() => void)[] = []
afterEach(() => {
  cleanups.splice(0).forEach((fn) => fn())
  document.body.innerHTML = ''
})

function mount(component: unknown, props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(component as never, props) })
  app.use(i18n)
  app.use(createVuetify({ components, directives }))
  app.mount(host)
  cleanups.push(() => app.unmount())
  return host
}

async function mountEditor(output: 'json' | 'html', value: JSONContent | string) {
  const model = ref<unknown>(value)
  const editorRef = ref<{ editor: LiveEditor } | null>(null)
  const host = document.createElement('div')
  document.body.appendChild(host)
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
  app.mount(host)
  cleanups.push(() => app.unmount())
  await waitFor(() => expect(editorRef.value?.editor).toBeTruthy())
  const toolbar = within(host.querySelector('[role="toolbar"]') as HTMLElement)
  return {
    saved: () => model.value,
    /** 选中第一段里第 from 到第 to 个字。 */
    select: (from: number, to: number) =>
      editorRef.value!.editor.commands.setTextSelection({ from: from + 1, to: to + 1 }),
    async press(name: string) {
      await fireEvent.click(toolbar.getByRole('button', { name }))
    },
    /** 打开一个弹层，点里面的一项。 */
    async choose(menu: string, item: string) {
      await fireEvent.click(toolbar.getByRole('button', { name: menu }))
      await fireEvent.click(await waitFor(() => within(openPopover()).getByRole('button', { name: item })))
    },
  }
}

/** 现在开着的那个弹层。 */
function openPopover(): HTMLElement {
  const all = document.querySelectorAll<HTMLElement>('.v-overlay--active .rt-pop')
  if (!all.length) throw new Error('no popover open')
  return all[all.length - 1]
}

const doc = (text: string): JSONContent => ({
  type: 'doc',
  content: [{ type: 'paragraph', content: [{ type: 'text', text }] }],
})

/** 存下来的东西再打开（只读那一侧），看见的格式。 */
async function reopen(saved: unknown) {
  const host = mount(TipTapViewer, { value: saved })
  await waitFor(() => expect(host.querySelector('.ProseMirror')).toBeTruthy())
  return host
}

function spanWith(host: HTMLElement, text: string): HTMLElement {
  const el = Array.from(host.querySelectorAll<HTMLElement>('span, mark, a, sub, sup')).find(
    (e) => e.textContent === text
  )
  if (!el) throw new Error(`no inline element holding ${text}`)
  return el
}

describe('工具栏设上的格式存下来再打开还在', () => {
  for (const output of ['json', 'html'] as const) {
    it(`字号、字体、颜色、高亮、对齐、上标、链接（存 ${output}）`, async () => {
      const ed = await mountEditor(output, output === 'json' ? doc('甲乙丙丁戊己庚辛') : '<p>甲乙丙丁戊己庚辛</p>')

      ed.select(0, 2)
      await ed.choose('字号', '18')
      ed.select(0, 2)
      await ed.choose('字体', 'Georgia')
      ed.select(2, 4)
      await ed.choose('文字颜色', '#f44336')
      ed.select(4, 5)
      await ed.choose('高亮颜色', '#ffeb3b')
      ed.select(5, 6)
      await ed.press('上标')
      await ed.choose('对齐方式', '居中对齐')

      ed.select(6, 8)
      await ed.press('链接')
      const form = await waitFor(() => {
        const el = document.querySelector<HTMLElement>('.v-overlay--active form')
        expect(el).toBeTruthy()
        return el!
      })
      await fireEvent.update(within(form).getByLabelText('链接地址'), 'https://example.com/lab')
      // 在地址框里回车，等于点「应用」。
      await fireEvent.submit(form)
      await nextTick()

      const host = await reopen(ed.saved())
      expect(spanWith(host, '甲乙').style.fontSize).toBe('18px')
      expect(spanWith(host, '甲乙').style.fontFamily).toBe('Georgia')
      expect(spanWith(host, '丙丁').style.color).not.toBe('')
      expect(spanWith(host, '戊').getAttribute('data-color')).toBe('#ffeb3b')
      expect(spanWith(host, '己').tagName).toBe('SUP')
      expect(host.querySelector('p')?.style.textAlign).toBe('center')
      expect(spanWith(host, '庚辛').getAttribute('href')).toBe('https://example.com/lab')
    })
  }
})
