/**
 * 一份文档读成分页（PDF）还是网页：窄的一格里 Word 默认读网页那一份（再由 `PreviewPage` 按
 * 屏宽重排），读者自己按过开关就照他按的来。
 *
 * 守的是两件容易一起坏的事：窄处 Word 没切过去（手机上又是六七像素的字），以及切过去之后
 * 读者按「分页视图」按不回来（开关只翻了一个没人读的变量）。
 */
import { createApp, defineComponent } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'

import { usePanelPreviewDocument } from './documentBytes'
import { createPreviewState } from './previewState'

vi.mock('../../lib/documentBytes', async () => {
  const { computed, ref } = await import('vue')
  return {
    useDocumentBytes: (source: { enabled: () => boolean }) => {
      const on = computed(() => source.enabled())
      return {
        bytes: computed(() => (on.value ? new ArrayBuffer(1) : null)),
        snapshot: ref(null),
        loading: ref(false),
        error: ref(''),
        rendererMissing: ref(false),
      }
    },
    useDocumentPage: (source: { enabled: () => boolean }) => {
      const on = computed(() => source.enabled())
      return {
        html: computed(() => (on.value ? '<p>页</p>' : null)),
        loading: ref(false),
        error: ref(''),
        rendererMissing: ref(false),
      }
    },
  }
})

const apps: ReturnType<typeof createApp>[] = []
afterEach(() => apps.splice(0).forEach((app) => app.unmount()))

function setup(path: string) {
  const state = createPreviewState()
  state.previewFile.value = { path, content: null, version: 'v1', bytes: 1, binary: true, too_large: false } as never
  let bundle!: ReturnType<typeof usePanelPreviewDocument>
  const app = createApp(
    defineComponent({
      setup() {
        bundle = usePanelPreviewDocument({ props: { topicId: 't1' } as never, state })
        return () => null
      },
    })
  )
  app.mount(document.createElement('div'))
  apps.push(app)
  return bundle
}

it('宽处 Word 照旧读分页那一份；一窄下来就换成网页那一份', () => {
  const doc = setup('报告.docx')
  doc.setNarrow(false)
  expect(doc.docPage.value).toBe(false)
  expect(doc.docBytes.value).not.toBeNull()

  doc.setNarrow(true)
  expect(doc.docPage.value).toBe(true)
  expect(doc.docPageHtml.value).not.toBeNull()
  // 同一时刻只取一种：PDF 那一边跟着停。
  expect(doc.docBytes.value).toBeNull()
})

it('窄处按「分页视图」回得去，而且之后宽窄再变也不自作主张切回来', () => {
  const doc = setup('报告.docx')
  doc.setNarrow(true)
  expect(doc.docPage.value).toBe(true)

  doc.toggleDocPage()
  expect(doc.docPage.value).toBe(false)
  doc.setNarrow(false)
  doc.setNarrow(true)
  expect(doc.docPage.value).toBe(false)

  doc.toggleDocPage()
  expect(doc.docPage.value).toBe(true)
})

it('表格和演示稿窄下来不换：它们各有为窄屏做的读法', () => {
  for (const path of ['成绩.xlsx', '汇报.pptx']) {
    const doc = setup(path)
    doc.setNarrow(true)
    expect(doc.docPage.value).toBe(false)
  }
})
