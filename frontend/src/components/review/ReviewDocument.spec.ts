/** 「改动」里的一份 Office 文件：指着一处写批注，和上一版对比。
 *
 * 规则：
 * - 待审阅时，在 Word 上选中一段文字、在幻灯片上选中字、在表格里点一个格子，就能对着
 *   那一处写批注；送出的批注记着第几页、第几页幻灯片或哪个格子，和那里的字。
 * - 不在待审阅时，选中文字不会冒出写批注的框。
 * - 「对比上一版」把这一版换成对比；被退回过的说明比的是退回时那一版。
 */
import type { DiffReview, OfficeComparison } from '@/types/reviewComment'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ReviewDocument from './ReviewDocument.vue'

import i18n, { setLocale } from '@/i18n'
import { revisionsBundle } from '@/test/panelBundles'

beforeEach(() => setLocale('zh-CN'))

// 真的页面要 pdf.js 画；这里只要它们往上报的那一下。
vi.mock('@/components/panels/preview/PreviewPages.vue', () => ({
  default: {
    name: 'PreviewPages',
    emits: ['quote'],
    template: `<button class="stub-pages" @click="$emit('quote', { text: '98 人', page: 3 })">pages</button>`,
  },
}))
vi.mock('@/components/panels/preview/PreviewSlides.vue', () => ({
  default: {
    name: 'PreviewSlides',
    emits: ['quote'],
    template: `<button class="stub-slides" @click="$emit('quote', { text: '实验结果', page: 2 })">slides</button>`,
  },
}))
vi.mock('@/components/panels/preview/PreviewSheet.vue', () => ({
  default: {
    name: 'PreviewSheet',
    emits: ['cell'],
    template: `<button class="stub-sheet" @click="$emit('cell', { address: 'C5', value: '96', sheet: '汇总' })">sheet</button>`,
  },
}))

const PAGES = { label: 'Word', icon: 'mdi-file-word', view: 'pages' as const }
const SHEET = { label: 'Excel', icon: 'mdi-file-excel', view: 'sheet' as const, sheet: 'workbook' as const }

function review(writable = true): DiffReview {
  return { comments: [], writable, me: 'alice', busy: false, agentName: '芝士' }
}

function mount(props: Record<string, unknown>) {
  const vuetify = createVuetify({ components, directives })
  return render(ReviewDocument, {
    props: { revs: revisionsBundle(), review: review(), docBytes: null, ...props } as never,
    global: { plugins: [vuetify, i18n] },
  })
}

function firstComment(emitted: Record<string, unknown[]>): unknown {
  return (emitted.comment as unknown[][] | undefined)?.[0]?.[0]
}

async function writeIt(text: string) {
  const field = document.querySelector('.comment-box textarea') as HTMLTextAreaElement
  await fireEvent.update(field, text)
  await fireEvent.click(screen.getByRole('button', { name: '添加批注' }))
}

describe('指着一处写批注', () => {
  it('Word 上选中的字，记成第几页和那段字', async () => {
    const { emitted } = mount({ path: 'docs/报告.docx', documentType: PAGES })
    await fireEvent.click(document.querySelector('.stub-pages')!)
    await writeIt('和问卷汇总表对不上')

    expect(firstComment(emitted())).toMatchObject({
      path: 'docs/报告.docx',
      place: 'p3',
      line_text: '98 人',
      body: '和问卷汇总表对不上',
    })
  })

  it('幻灯片上选中的字，记成第几页幻灯片', async () => {
    const { emitted } = mount({ path: 'docs/答辩.pptx', documentType: PAGES })
    await fireEvent.click(document.querySelector('.stub-slides')!)
    await writeIt('结果页要加样本量')

    expect(firstComment(emitted())).toMatchObject({ place: 's2', line_text: '实验结果' })
  })

  it('表格里点的格子，记成「表!格」和格子里的值', async () => {
    const { emitted } = mount({ path: 'docs/问卷汇总.xlsx', documentType: SHEET })
    await fireEvent.click(document.querySelector('.stub-sheet')!)
    await writeIt('合计不对')

    expect(firstComment(emitted())).toMatchObject({ place: '汇总!C5', line_text: '96' })
  })

  it('不在待审阅时，选中文字不会冒出写批注的框', async () => {
    mount({ path: 'docs/报告.docx', documentType: PAGES, review: review(false) })
    await fireEvent.click(document.querySelector('.stub-pages')!)

    expect(document.querySelector('.comment-box')).toBeNull()
  })
})

describe('和上一版对比', () => {
  const comparison: OfficeComparison = {
    kind: 'word',
    new_file: false,
    identical: false,
    changed: 1,
    against: 'returned',
    rows: [
      {
        op: 'changed',
        before: 1,
        after: 1,
        pieces: [
          { op: 'delete', text: '98' },
          { op: 'insert', text: '96' },
        ],
      },
    ],
    formatting: [],
  }

  it('点「对比上一版」交给外面去取', async () => {
    const { emitted } = mount({ path: 'docs/报告.docx', documentType: PAGES, canCompare: true })
    await fireEvent.click(screen.getByRole('button', { name: /对比上一版/ }))
    expect(emitted()['toggle-compare']).toHaveLength(1)
  })

  it('对比时说明比的是退回时那一版，画出改掉和加上的字', () => {
    mount({ path: 'docs/报告.docx', documentType: PAGES, canCompare: true, comparing: true, comparison })

    expect(screen.getByText('对比退回时那一版')).toBeTruthy()
    expect(screen.getByText('98').className).toContain('delete')
    expect(screen.getByText('96').className).toContain('insert')
    expect(document.querySelector('.stub-pages')).toBeNull()
  })
})
