/**
 * 代码那一档读的是 `head`，不是 `content`。
 *
 * 一份超过编辑器上限的文本文件，`content` 是 null（编辑器要是拿一段前缀上屏，保存就把
 * 前缀写回去、把整份文件截断了），而读者该看到的是它的开头。这两条盯的就是这条分界：
 * 有大文件的开头就摆出来并说清底下还有，连开头都读不出（二进制）才说它不是文本。
 */
import type { Component } from 'vue'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PreviewDocStage from './PreviewDocStage.vue'

import { setLocale } from '@/i18n'
import { previewBundles } from '@/test/panelBundles'

const width = ref(1200)
vi.mock('@vueuse/core', async (orig) => ({
  ...(await orig<typeof import('@vueuse/core')>()),
  useElementSize: () => ({ width, height: ref(800), stop: () => {} }),
}))
vi.mock('./PreviewPages.vue', () => ({
  default: defineComponent({ setup: () => () => h('div', { 'data-testid': 'pages' }) }),
}))

const vuetify = createVuetify({ components, directives })

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function mount(
  previewFile: Record<string, unknown>,
  kind: Record<string, unknown> = { label: 'Python', icon: 'mdi-language-python', view: 'code', language: 'python' }
) {
  const bundles = previewBundles()
  return render(PreviewDocStage as unknown as Component, {
    props: {
      topicId: 't1',
      projectId: 'p1',
      documentType: kind,
      documentName: 'run.py',
      documentSuffix: '.py',
      previewFile,
      docBytes: null,
      docLoading: false,
      docError: '',
      docRendererMissing: false,
      revs: bundles.revs,
      fileHistory: bundles.fileHistory,
      showHistory: false,
    },
    global: {
      plugins: [vuetify],
      stubs: { VMenu: { template: '<div><slot name="activator" :props="{}" /><slot /></div>' } },
    },
  })
}

it('大文件摆的是它的开头，并且说清底下还有', async () => {
  const ui = mount({
    path: 'run.py',
    content: null,
    head: 'print("hello")\n',
    version: 'v1',
    bytes: 3_000_000,
    binary: false,
    too_large: true,
  })

  // 读者看得见那几个字（高亮会把它切成几段，所以按整块的文字看），也知道底下还有。
  expect(ui.getByTestId('code-view').textContent).toContain('print')
  expect(ui.getByText(/只摆了开头/)).toBeTruthy()
})

it('连开头都读不成文本时，才说它不是文本', async () => {
  const ui = mount({
    path: 'blob.bin',
    content: null,
    head: null,
    version: 'v1',
    bytes: 3_000_000,
    binary: false,
    too_large: true,
  })

  expect(ui.getByText('这个文件不是文本')).toBeTruthy()
  expect(ui.queryByText(/只摆了开头/)).toBeNull()
})

it('大 markdown 也摆开头，并且说清底下还有', async () => {
  // 同一份契约的另一档：markdown 读不出来时说「不是文本」、空的说「文件是空的」，
  // 但超大的一份有开头就该照常画出来——不能因为它是 markdown 就退回「太大」。
  const ui = mount(
    {
      path: 'notes.md',
      content: null,
      head: '# 标题\n\n正文第一段\n',
      version: 'v1',
      bytes: 3_000_000,
      binary: false,
      too_large: true,
    },
    { label: 'Markdown', icon: 'mdi-language-markdown', view: 'markdown' }
  )

  // markdown 那一档的正文是异步画出来的，等它落下来。
  await waitFor(() => expect(ui.getByTestId('markdown').textContent).toContain('标题'))
  expect(ui.getByText(/只摆了开头/)).toBeTruthy()
})

it('只带 content 的调用方照样画得出来', async () => {
  // `head` 是可选的：整份装得下时后端两个字段给的是同一段。只要 `content` 的调用方
  // （和夹具）不能被渲染成空白。
  const ui = mount({
    path: 'run.py',
    content: 'print("hi")\n',
    version: 'v1',
    bytes: 12,
    binary: false,
    too_large: false,
  })

  expect(ui.getByTestId('code-view').textContent).toContain('print')
})
