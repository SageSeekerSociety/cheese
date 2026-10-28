/**
 * Markdown 这一档的几件只有它自己有的事：空文件、大到正文取不回来的文件、
 * 长文的目录，以及代码块的折行/复制。
 *
 * 为什么单独一个文件：`PreviewDocStage` 的分支是「按文件类型」分的，这几条判据只对
 * markdown 成立（PDF 没有「空正文」这一说），和 `compact`、`versions` 那几份用例
 * 关注的不是同一件事。
 */
import type { Component } from 'vue'

import { ref } from 'vue'
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

const vuetify = createVuetify({ components, directives })

beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  cleanup()
  width.value = 1200
})

const MARKDOWN = { label: 'Markdown', icon: 'mdi-language-markdown-outline', view: 'markdown' } as const

function mount(previewFile: Record<string, unknown>) {
  const bundles = previewBundles()
  return render(PreviewDocStage as unknown as Component, {
    props: {
      topicId: 't1',
      projectId: 'p1',
      documentType: MARKDOWN,
      documentName: 'notes.md',
      documentSuffix: 'md',
      previewFile: {
        path: 'output/notes.md',
        version: 'v1',
        bytes: 10,
        binary: false,
        too_large: false,
        ...previewFile,
      },
      docBytes: null,
      docLoading: false,
      docError: '',
      docRendererMissing: false,
      downloadError: '',
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

it('空文件说一句「这个文件是空的」，不是一片空白', async () => {
  // 空白和「预览坏了」在屏幕上长得一模一样：读者分不出是自己打开了一份空文件，
  // 还是这一格没画出来。
  const ui = mount({ content: '\n\n' })
  await waitFor(() => expect(ui.getByText('这个文件是空的')).toBeTruthy())
})

it('大到正文没取回来的文件说清原因，并给下载', async () => {
  // `too_large` 的 content 也是 null，但那份文件是文本、只是这次没取——不能落到
  // 「不是文本」那一句上。
  const ui = mount({ content: null, too_large: true })
  await waitFor(() => expect(ui.getByText('这份文件太大，正文没有取回来')).toBeTruthy())
  // 读不到正文时给出一条去处：这一档的「下载」摊在文档条上和提示里各有一处。
  expect(ui.getAllByText('下载').length).toBeGreaterThan(0)
})

it('不是文本的 markdown（读成 null 且没超限）仍走原来那句', async () => {
  const ui = mount({ content: null, too_large: false })
  await waitFor(() => expect(ui.queryByText('这份文件太大，正文没有取回来')).toBeNull())
})

it('长文的目录：三个以上标题才出现，点了滚到那一节', async () => {
  const source = '# 一\n\n正文。\n\n## 二\n\n正文。\n\n## 三\n\n正文。\n'
  const ui = mount({ content: source })
  const md = await waitFor(() => ui.getByTestId('markdown'))
  await waitFor(() => expect(md.querySelectorAll('h2').length).toBe(2), { timeout: 5000 })
  const trigger = await waitFor(() => ui.getByTestId('md-outline'))
  expect(trigger.textContent).toContain('大纲')
  const items = ui.getAllByRole('menuitem').map((el) => el.textContent ?? '')
  expect(items).toEqual(['一', '二', '三'])
})

it('两个标题的文档不摆目录：那是占位，不是导航', async () => {
  const ui = mount({ content: '# 一\n\n正文。\n\n## 二\n\n正文。\n' })
  const md = await waitFor(() => ui.getByTestId('markdown'))
  await waitFor(() => expect(md.querySelectorAll('h2').length).toBe(1), { timeout: 5000 })
  expect(ui.queryByTestId('md-outline')).toBeNull()
})

it('换一份文件，上一份的目录不跟过来', async () => {
  const ui = mount({ content: '# 甲\n\n正文。\n\n## 乙\n\n正文。\n\n## 丙\n\n正文。\n' })
  await waitFor(() => expect(ui.getByTestId('md-outline')).toBeTruthy())
  await ui.rerender({
    documentType: MARKDOWN,
    documentName: 'other.md',
    documentSuffix: 'md',
    previewFile: {
      path: 'output/other.md',
      content: '# 只有一个标题\n',
      version: 'v1',
      bytes: 10,
      binary: false,
      too_large: false,
    },
  })
  // 新正文还没画出来时，那颗按钮不该已经按着旧的章节跳。
  expect(ui.queryByTestId('md-outline')).toBeNull()
})

// ---- 代码块的两颗 ----
//
// 对话栏里的 Markdown 一直带「自动换行 / 复制」；预览这一档的 `MarkdownView` 漏了
// `copyCode`，同一段命令在这儿只能左右拖。这几条盯的是这一档有没有交上去。

const CODE = '# 命令\n\n```bash\ncheese worktree a-very-long-id && cd "$T" && git log --oneline\n```\n'

it('预览里的代码块带「自动换行」和「复制」', async () => {
  const ui = mount({ content: CODE })
  const md = await waitFor(() => ui.getByTestId('markdown'))
  await waitFor(() => expect(md.querySelector('pre')).toBeTruthy(), { timeout: 5000 })
  const bar = md.querySelector('.md-pre-bar')
  expect(bar).toBeTruthy()
  const labels = Array.from(bar!.querySelectorAll('button')).map((b) => b.textContent ?? '')
  expect(labels.some((text) => text.includes('自动换行'))).toBe(true)
  expect(labels.some((text) => text.includes('复制'))).toBe(true)
})

it('代码块的正文照原文画，没有被工具条吃掉', async () => {
  const ui = mount({ content: CODE })
  const md = await waitFor(() => ui.getByTestId('markdown'))
  await waitFor(() => expect(md.querySelector('pre')).toBeTruthy(), { timeout: 5000 })
  expect(md.querySelector('pre')!.textContent).toContain('cheese worktree a-very-long-id')
})
