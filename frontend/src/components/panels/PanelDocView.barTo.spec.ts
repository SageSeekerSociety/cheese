// @vitest-environment jsdom
// 项目目标那一页（ProjectDocsView）是保活的，它把文档的顶栏画到页面自己那一行里
// （`bar-to`）。人离开那一页时它还活着、只是不在文档里；文档要是这时候才打开（读得慢、
// 人走得快），顶栏找的那个位置在一棵摘下来的树里。这里钉住的是：回到那一页，顶栏在
// 它该在的那一行里；在这期间文档照常更新、关掉，页面不坏。
import { defineComponent, h, KeepAlive, nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { DOC_TOPIC, docPanelProps } from '../../views/demo/catalogFixtures'

import PanelDocView from './PanelDocView.vue'

import { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it('a doc opened while its kept-alive page is away puts its top bar in the page bar once the page is back', async () => {
  const errors: unknown[] = []
  const page = ref<'docs' | 'elsewhere'>('docs')
  const docOpen = ref(false)
  const loading = ref(true)

  const DocsPage = defineComponent({
    name: 'DocsPage',
    setup: () => () =>
      h('div', [
        h('div', { id: 'docs-page-bar', 'data-testid': 'page-bar' }),
        docOpen.value
          ? h(PanelDocView, { ...docPanelProps(), topic: DOC_TOPIC, barTo: '#docs-page-bar', loading: loading.value })
          : null,
      ]),
  })
  const Elsewhere = defineComponent({ name: 'Elsewhere', setup: () => () => h('p', 'elsewhere') })
  const Shell = defineComponent({
    setup: () => () => h(KeepAlive, { include: ['DocsPage'] }, [page.value === 'docs' ? h(DocsPage) : h(Elsewhere)]),
  })

  render(Shell, {
    global: {
      plugins: [createVuetify({ components, directives })],
      config: { errorHandler: (err) => void errors.push(err) },
    },
  })

  // 人离开了项目目标那一页，文档这时才打开，接着照常更新。
  page.value = 'elsewhere'
  await nextTick()
  docOpen.value = true
  await nextTick()
  await nextTick()
  loading.value = false
  await nextTick()

  // 回到那一页：顶栏在页面那一行里。
  page.value = 'docs'
  await nextTick()
  await nextTick()
  expect(screen.getByTestId('page-bar').querySelector('.doc-top')).not.toBeNull()

  // 再走开、文档关掉，都不出错。
  page.value = 'elsewhere'
  await nextTick()
  docOpen.value = false
  await nextTick()
  expect(errors).toEqual([])
})
