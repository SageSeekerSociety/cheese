import { createApp, h, nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { afterAll, beforeAll, expect, it, vi } from 'vitest'

import TipTapEditor from './TipTapEditor.vue'

import i18n from '@/i18n'

// vuetify-pro-tiptap's toolbar and ProseMirror both reach for browser APIs that
// happy-dom does not implement. Neither is what these tests are about.
beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () =>
      ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} }) as never
    Range.prototype.getBoundingClientRect = () =>
      ({ top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0 }) as never
  }
})
afterAll(() => vi.unstubAllGlobals())

// A document that exercises one node/mark from each corner of the configured
// extension list. Anything tiptap has no extension for is dropped when the
// document is loaded, so these either all survive or the editor came up with an
// empty schema.
const doc = {
  type: 'doc',
  content: [
    { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: '标题' }] },
    { type: 'paragraph', content: [{ type: 'text', marks: [{ type: 'bold' }], text: '粗体' }] },
    {
      type: 'table',
      content: [
        {
          type: 'tableRow',
          content: [{ type: 'tableCell', content: [{ type: 'paragraph', content: [{ type: 'text', text: '格子' }] }] }],
        },
      ],
    },
    { type: 'blockquote', content: [{ type: 'paragraph', content: [{ type: 'text', text: '引用' }] }] },
    { type: 'codeBlock', content: [{ type: 'text', text: 'print(1)' }] },
  ],
}

async function mountEditor() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({
    render: () => h(TipTapEditor, { output: 'json', modelValue: doc, ref: 'editor' }),
  })
  app.use(createVuetify({ components, directives }))
  app.use(i18n)
  const vm = app.mount(host) as unknown as { $refs: { editor: { editor: { commands: Record<string, unknown> } } } }
  await nextTick()
  await nextTick()
  return {
    html: host.innerHTML,
    editor: vm.$refs.editor.editor,
    teardown: () => {
      app.unmount()
      host.remove()
    },
  }
}

it('renders every configured node and mark on the very first mount', async () => {
  const { html, teardown } = await mountEditor()
  try {
    expect(html).toContain('<h2')
    expect(html).toContain('<strong')
    expect(html).toContain('<table')
    expect(html).toContain('<blockquote')
    expect(html).toContain('<pre')
    expect(html).toContain('标题')
    expect(html).toContain('格子')
  } finally {
    teardown()
  }
})

it('exposes the app-specific attachment image command on the first mount', async () => {
  const { editor, teardown } = await mountEditor()
  try {
    expect(typeof editor.commands.setImage).toBe('function')
  } finally {
    teardown()
  }
})

it('keeps working on a second, independently created app', async () => {
  const first = await mountEditor()
  first.teardown()
  const second = await mountEditor()
  try {
    expect(second.html).toContain('<table')
    expect(typeof second.editor.commands.setImage).toBe('function')
  } finally {
    second.teardown()
  }
})
