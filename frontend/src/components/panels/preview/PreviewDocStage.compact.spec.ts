/**
 * 文档条在窄的一格里（手机、桌面窄右栏）：历史、下载这些收进「…」，条始终一行；
 * 挂在写着文件名的页签上时不再重复文件名。宽的一格照旧平铺。
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

// happy-dom 没有布局，量出来是 0；宽度由这里给。
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
afterEach(() => {
  cleanup()
  width.value = 1200
})

function mount(namedTab = false) {
  const bundles = previewBundles()
  return render(PreviewDocStage as unknown as Component, {
    props: {
      topicId: 't1',
      projectId: 'p1',
      documentType: { label: 'PDF', icon: 'mdi-file-pdf-box', view: 'pages' },
      documentName: 'preview-2.pdf',
      documentSuffix: '.pdf',
      previewFile: { path: 'output/preview-2.pdf', content: null, size: 10, mime: 'application/pdf' },
      docBytes: new ArrayBuffer(8),
      docLoading: false,
      docError: '',
      docRendererMissing: false,
      downloadError: '',
      revs: bundles.revs,
      fileHistory: bundles.fileHistory,
      showHistory: false,
      namedTab,
    },
    // 菜单内容直接摊开画（happy-dom 里 v-menu 的浮层要 visualViewport）。
    global: {
      plugins: [vuetify],
      stubs: { VMenu: { template: '<div><slot name="activator" :props="{}" /><slot /></div>' } },
    },
  })
}

it('宽：历史、下载平铺在条上，没有「…」，文件名在', () => {
  const ui = mount()
  expect(ui.getByTestId('file-history')).toBeTruthy()
  expect(ui.queryByTestId('doc-more')).toBeNull()
  expect(ui.getByText('preview-2.pdf')).toBeTruthy()
})

it('窄：历史、下载收进「…」，条上不再平铺', async () => {
  width.value = 390
  const ui = mount()
  await waitFor(() => expect(ui.getByTestId('doc-more')).toBeTruthy())
  expect(ui.queryByTestId('file-history')).toBeNull()
  const items = ui.getAllByRole('menuitem').map((el) => el.textContent ?? '')
  expect(items.some((text) => text.includes('历史'))).toBe(true)
  expect(items.some((text) => text.includes('下载'))).toBe(true)
})

it('页签上已经写着文件名：条上不重复，类型标签还在', () => {
  const ui = mount(true)
  expect(ui.queryByText('preview-2.pdf')).toBeNull()
  expect(ui.getByText('PDF')).toBeTruthy()
})
