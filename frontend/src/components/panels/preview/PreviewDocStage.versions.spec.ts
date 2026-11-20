/**
 * 文档条上的版本：文件名旁「第 N 版 · 谁 · 什么时候」，下面那一条提示 / 对比 / 旧版，
 * 对比态里正文换成标出增删的整篇。取数和判断都在 `versions` 那一包里，这里只核摆在哪、
 * 点了发生什么。
 */
import type { Component } from 'vue'
import type { PreviewVersionsBundle, VersionCompare, VersionEntry } from '@/composables/panelPreview/versions'

import { computed, defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PreviewDocStage from './PreviewDocStage.vue'

import { setLocale } from '@/i18n'
import { versionDiff } from '@/lib/versionDiff'
import { previewBundles } from '@/test/panelBundles'

const width = ref(1200)
vi.mock('@vueuse/core', async (orig) => ({
  ...(await orig<typeof import('@vueuse/core')>()),
  useElementSize: () => ({ width, height: ref(800), stop: () => {} }),
}))
vi.mock('../../common/MarkdownView.vue', () => ({
  default: defineComponent({
    props: { source: { type: String, default: '' } },
    setup: (props) => () => h('div', { 'data-testid': 'md' }, props.source),
  }),
}))

const vuetify = createVuetify({ components, directives })

beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  cleanup()
  width.value = 1200
})

const ENTRIES: VersionEntry[] = [
  {
    id: 'r2',
    seq: 2,
    author: '芝士',
    at: new Date().toISOString(),
    note: '按批注改',
    current: true,
    latest: true,
    lastSeen: false,
  },
  {
    id: 'r1',
    seq: 1,
    author: '王长鑫',
    at: new Date().toISOString(),
    note: null,
    current: false,
    latest: false,
    lastSeen: true,
  },
]

function fakeVersions(over: { hint?: boolean; compare?: VersionCompare | null; oldSeq?: number | null } = {}) {
  const compare = ref<VersionCompare | null>(over.compare ?? null)
  const calls: string[] = []
  const bundle = {
    list: computed(() => ENTRIES),
    current: computed(() => ENTRIES[0]),
    canCompare: computed(() => true),
    canView: computed(() => true),
    hint: computed(() => (over.hint ? { base: 1, count: 3, unit: 'place' as const } : null)),
    compare: computed(() => compare.value),
    oldView: computed(() => (over.oldSeq ? { seq: over.oldSeq, kind: 'markdown' as const, text: '# 旧的' } : null)),
    oldError: ref(''),
    changedCells: computed(() => null),
    changedBy: computed(() => '芝士'),
    openCompare: (base?: number) => calls.push(`open:${base ?? ''}`),
    exitCompare: () => calls.push('exit'),
    dismissHint: () => calls.push('dismiss'),
    choose: (seq: number) => calls.push(`choose:${seq}`),
    backToLatest: () => calls.push('back'),
  }
  return { bundle: bundle as unknown as PreviewVersionsBundle, calls }
}

function mount(versions: PreviewVersionsBundle) {
  const bundles = previewBundles()
  return render(PreviewDocStage as unknown as Component, {
    props: {
      topicId: 't1',
      projectId: 'p1',
      documentType: { label: 'Markdown', icon: 'mdi-language-markdown-outline', view: 'markdown' },
      documentName: '招募方案.md',
      documentSuffix: 'md',
      previewFile: { path: '样例/招募方案.md', content: '# 新的', head: '# 新的', version: 'v2' },
      docBytes: null,
      docLoading: false,
      docError: '',
      docRendererMissing: false,
      revs: bundles.revs,
      fileHistory: bundles.fileHistory,
      showHistory: false,
      versions,
    },
    global: {
      plugins: [vuetify],
      stubs: { VMenu: { template: '<div><slot name="activator" :props="{}" /><slot /></div>' } },
    },
  })
}

it('宽：文件名旁写着第几版、谁、什么时候；「历史」收进版本菜单，条上换成「对比」', () => {
  const { bundle } = fakeVersions()
  const ui = mount(bundle)
  expect(ui.getByTestId('version-picker').textContent).toContain('第 2 版')
  expect(ui.getByTestId('version-picker').textContent).toContain('芝士')
  expect(ui.getByTestId('version-compare')).toBeTruthy()
  // 版本菜单底下那一条就是保存历史；条上不再平铺一颗「历史」。
  expect(ui.getAllByTestId('file-history')).toHaveLength(1)
  expect(ui.getByTestId('file-history').textContent).toContain('保存历史')
})

it('窄：版本行只写「第 N 版」，谁和什么时候进 title；「对比」收进「…」', async () => {
  width.value = 390
  const { bundle } = fakeVersions()
  const ui = mount(bundle)
  await waitFor(() => expect(ui.getByTestId('doc-more')).toBeTruthy())
  const picker = ui.getByTestId('version-picker')
  expect(picker.textContent).not.toContain('芝士')
  expect(picker.getAttribute('title')).toContain('芝士')
  expect(ui.queryByTestId('version-compare')).toBeNull()
  expect(ui.getAllByRole('menuitem').some((el) => el.textContent?.includes('对比'))).toBe(true)
})

it('菜单里点一版：交给取数那一层决定看它还是比它', async () => {
  const { bundle, calls } = fakeVersions()
  const ui = mount(bundle)
  const row = ui.container.querySelector('[data-seq="1"]') as HTMLElement
  expect(row.textContent).toContain('你上次看到这')
  await fireEvent.click(row)
  expect(calls).toContain('choose:1')
})

it('提示条：「本版共修改 3 处（对比第 1 版）」，查看改动进对比，收起记下看过了', async () => {
  const { bundle, calls } = fakeVersions({ hint: true })
  const ui = mount(bundle)
  expect(ui.getByTestId('version-strip').textContent).toContain('本版共修改 3 处（对比第 1 版）')
  await fireEvent.click(ui.getByTestId('version-view-changes'))
  await fireEvent.click(ui.getByTestId('version-dismiss'))
  expect(calls).toEqual(['open:', 'dismiss'])
})

it('对比态：条上写两版和图例，正文换成标出增删的整篇，能一处处跳', async () => {
  const diff = versionDiff('甲\n乙\n丙\n丁', '甲\n乙改\n丙\n丁\n戊', { collapse: false })
  const { bundle, calls } = fakeVersions({
    compare: {
      base: 1,
      target: 2,
      loading: false,
      error: '',
      changes: { kind: 'text', markdown: false, diff, count: 2 },
    },
  })
  const ui = mount(bundle)
  const strip = ui.getByTestId('version-strip')
  expect(strip.dataset.mode).toBe('compare')
  expect(strip.textContent).toContain('版本对比：第 1 版 → 第 2 版')
  expect(strip.textContent).toContain('绿色为新增，删除线为移除')
  expect(strip.textContent).toContain('第 1/2 处')
  expect(ui.getByTestId('version-diff').querySelectorAll('[data-change]')).toHaveLength(2)
  expect(ui.getByTestId('version-diff').querySelector('ins')?.textContent).toBe('改')
  await fireEvent.click(ui.getByTestId('version-next'))
  expect(strip.textContent).toContain('第 2/2 处')
  await fireEvent.click(ui.getByTestId('version-exit'))
  expect(calls).toContain('exit')
})

it('看旧版：条上说明不是当前版，正文是那一版，回到当前版交给取数那一层', async () => {
  const { bundle, calls } = fakeVersions({ oldSeq: 1 })
  const ui = mount(bundle)
  expect(ui.getByTestId('version-strip').textContent).toContain('正在看第 1 版，不是当前版')
  expect(ui.getByTestId('markdown').textContent).toBe('# 旧的')
  await fireEvent.click(ui.getByTestId('version-back'))
  expect(calls).toContain('back')
})
