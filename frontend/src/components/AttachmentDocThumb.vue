<script setup lang="ts">
// 一份文档的第一页，画在附件块那个方格里。
//
// 发之前真正要确认的是「附的是不是那一份」，而 `report.docx` 这个名字答不了，
// 封面能。
//
// 两种来源，同一张画布。PDF 浏览器自己就读得了，直接取原始字节——走
// `previewFileBytes` 而不是图片那个地址助手，因为原始端点对非图片只在
// `download=true` 时才发字节（PreviewPages 也是为这个用它）。Word 和幻灯片
// 浏览器画不了，平台先用 LibreOffice 转一次：一份约 2.5 秒，后端按内容哈希缓存，
// 所以同一个文件只转一次，而且读者后面在预览面板里打开它时也是这一份。
//
// 这 2.5 秒里方格不转圈，就摆这个类型的图标：转两秒半的圈比直接给一个说明类型的
// 图标更难受，而且图标本身已经是一个正确的答案，页面来了再替上去。
import { onBeforeUnmount, ref, watch } from 'vue'

import { previewDocumentPdf, previewFileBytes } from '../api'
import { fileIcon, NEEDS_CONVERSION, suffixOf } from '../lib/fileKind'

type PdfLib = typeof import('pdfjs-dist')

const props = defineProps<{
  topicId: string | null
  path: string
}>()

let lib: PdfLib | null = null
// Every load gets a number, so a reply that arrives after the props moved on
// cannot draw over the tile that replaced it.
let generation = 0

const canvas = ref<HTMLCanvasElement | null>(null)
const failed = ref(false)
const drawn = ref(false)

/** 画不出来的时候这个方格里摆什么。给这个文件自己的类型图标，而不是一律 PDF：
 *  一份转换失败的 .docx 顶着 PDF 图标，比没有缩略图更容易让人读错。 */
const mark = () => fileIcon(props.path)

/** 这份文档的 PDF 字节：本来就是 PDF 就直接取，否则让平台转一次。 */
function pdfBytes(topicId: string, path: string): Promise<ArrayBuffer> {
  return NEEDS_CONVERSION.has(suffixOf(path)) ? previewDocumentPdf(topicId, path) : previewFileBytes(topicId, path)
}

/** pdf.js needs its worker pinned before the first getDocument, or it guesses
 *  an address it cannot reach. Shared across tiles — loading it is the
 *  expensive part, and it is the same worker either way. */
async function library(): Promise<PdfLib> {
  if (lib) return lib
  const [pdfjs, workerUrl] = await Promise.all([
    import('pdfjs-dist'),
    import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
  ])
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl.default
  lib = pdfjs
  return lib
}

async function draw() {
  const mine = ++generation
  failed.value = false
  drawn.value = false
  if (!props.topicId || !props.path) return
  try {
    const [bytes, pdfjs] = await Promise.all([pdfBytes(props.topicId, props.path), library()])
    if (mine !== generation) return
    // pdf.js takes ownership of the buffer it is handed, so it gets a copy —
    // otherwise re-opening the same bytes finds them empty.
    const task = pdfjs.getDocument({ data: bytes.slice(0) })
    try {
      const doc = await task.promise
      if (mine !== generation) return
      const page = await doc.getPage(1)
      const el = canvas.value
      if (!el || mine !== generation) return
      // Cover the square: scale by the LONGER side so a portrait page fills the
      // tile and gets cropped, the way an image thumbnail does, rather than
      // sitting in the middle of two grey bars.
      const base = page.getViewport({ scale: 1 })
      // The box's own width, so the page is rendered at the size it is shown
      // at. The fallback is for an environment with no layout at all (jsdom),
      // where nothing is drawn anyway and 0 would only make `scale` collapse.
      const tile = el.clientWidth || 40
      const ratio = Math.min(window.devicePixelRatio || 1, 2)
      const scale = (tile / Math.min(base.width, base.height)) * ratio
      const viewport = page.getViewport({ scale })
      el.width = Math.max(1, Math.round(viewport.width))
      el.height = Math.max(1, Math.round(viewport.height))
      const context = el.getContext('2d')
      if (!context) return
      await page.render({ canvas: el, canvasContext: context, viewport }).promise
      if (mine !== generation) return
      drawn.value = true
    } finally {
      void task.destroy()
    }
  } catch {
    // A thumbnail that cannot be drawn is not an error worth interrupting the
    // composer for — the tile falls back to naming the file.
    if (mine === generation) failed.value = true
  }
}

watch(() => [props.topicId, props.path], draw, { immediate: true })
onBeforeUnmount(() => {
  generation += 1
})
</script>

<template>
  <span class="att-face">
    <canvas ref="canvas" class="doc-thumb__page" :class="{ 'doc-thumb__page--ready': drawn }" />
    <v-icon v-if="!drawn" size="22">{{ mark() }}</v-icon>
  </span>
</template>

<style scoped>
/* The box itself is `.att-face` (style.css), shared by every attachment state;
   only what sits inside it is this component's.
   Absolute, and hidden with opacity rather than `display: none`: an undrawn
   canvas would otherwise measure 0 wide, and `draw()` reads that width to
   decide how large a page to render. */
.doc-thumb__page {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  opacity: 0;
}
.doc-thumb__page--ready {
  opacity: 1;
}
/* 页面上不压类型角标：画出来的封面本身就说明它是一份文档，而那个标签还需要一个
   不在字号尺度里的字号。 */
</style>
