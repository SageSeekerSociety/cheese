/// <reference types="node" />
/**
 * 一页的文字在 WebKit 上也要读得出来。
 *
 * Safari 和 mac 桌面端都没有 `ReadableStream[Symbol.asyncIterator]`（2026-10 仍是如此），
 * 而 pdf.js 的 `page.getTextContent()` 内部是 `for await (const chunk of 流)`：那边一调
 * 就抛 `undefined is not a function`。对话里的文件卡片在渲染中取首页两行摘要，抛出去
 * 被时间线的 ErrorBoundary 接住，整段对话被换成「无法显示这块内容」。
 *
 * 这条测试把那个特性从原型上摘掉，再读一页的文字——修前这里会抛，修后拿得到。
 * 用的是真的 pdf.js 和一份真的最小 PDF：摘的是内核能力，不是给它喂假数据。
 */
import { createRequire } from 'node:module'

import { createApp, defineComponent, h } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'

import { useSlidesPdf } from './useSlidesPdf'

// pdf.js 的 worker 是给浏览器准备的资源：测试里没有能启动的 Worker，它会退回同线程的
// 「fake worker」并按这个地址去 import。`?url` 在测试里给的是站内路径，import 不了，
// 所以钉成设备上的真文件。
const require = createRequire(import.meta.url)
vi.mock('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url', () => ({
  default: `file://${require.resolve('pdfjs-dist/legacy/build/pdf.worker.min.mjs')}`,
}))

/** 一份一页、一行的 PDF：内容就是下面那句话，没有别的可读的东西。 */
const PAGE_LINE = 'Hello from the PDF'
function minimalPdf(): ArrayBuffer {
  const content = `BT /F1 18 Tf 20 100 Td (${PAGE_LINE}) Tj ET`
  const objects = [
    '<</Type/Catalog/Pages 2 0 R>>',
    '<</Type/Pages/Kids[3 0 R]/Count 1>>',
    '<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>',
    `<</Length ${content.length}>>stream\n${content}\nendstream`,
    '<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>',
  ]
  let body = '%PDF-1.4\n'
  const offsets: number[] = []
  objects.forEach((object, index) => {
    offsets.push(body.length)
    body += `${index + 1} 0 obj\n${object}\nendobj\n`
  })
  const xref = body.length
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  for (const offset of offsets) body += `${String(offset).padStart(10, '0')} 00000 n \n`
  body += `trailer\n<</Size ${objects.length + 1}/Root 1 0 R>>\nstartxref\n${xref}\n%%EOF\n`
  return new TextEncoder().encode(body).buffer as ArrayBuffer
}

let app: ReturnType<typeof createApp> | null = null
afterEach(() => {
  app?.unmount()
  app = null
})

/** 挂上这份文档，等它解析完（页数出来、或明确报错），再把控制权交回去。 */
async function openPdf() {
  let pdf!: ReturnType<typeof useSlidesPdf>
  const Host = defineComponent({
    setup() {
      pdf = useSlidesPdf(() => minimalPdf())
      return () => h('div')
    },
  })
  app = createApp(Host)
  app.mount(document.createElement('div'))
  await vi.waitFor(() => expect(pdf.count.value > 0 || pdf.failure.value !== '').toBe(true), 30_000)
  expect(pdf.failure.value).toBe('')
  return pdf
}

it('内核没有流异步迭代（Safari、桌面端）时，一页的文字照样读得出来', async () => {
  const proto = ReadableStream.prototype as unknown as Record<symbol, unknown>
  const saved = proto[Symbol.asyncIterator]
  delete proto[Symbol.asyncIterator]
  try {
    const pdf = await openPdf()
    expect(await pdf.pageText(1)).toBe(PAGE_LINE)
  } finally {
    proto[Symbol.asyncIterator] = saved
  }
}, 60_000)
