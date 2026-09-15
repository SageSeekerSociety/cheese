<script setup lang="ts">
// 分页文档的阅读视图：PDF 本身，以及 Word、幻灯片转换成 PDF 之后的样子。
//
// 画布之上盖一层透明的文字层，这是这个组件唯一非显然的地方，也是它存在的理由。
// 没有它，一页文档就是一张图：读的人不能选、不能搜、不能复制一句话给芝士看。
// 有了它，读者指着一句话说「这里不对」这件事才成立——选中的原文就是交给芝士的坐标。
//
// 页面按需渲染。一份几十页的文档一次性全画出来，等待的是空白，而且大多数页永远
// 不会被看到。

import type { PDFDocumentLoadingTask, PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist'

import { nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'

type PdfLib = typeof import('pdfjs-dist')

const props = defineProps<{
  /** 文档的原始字节。换一份文档就换一个 ArrayBuffer。 */
  data: ArrayBuffer | null
}>()

const emit = defineEmits<{
  /** 读者选中了一段原文，附带它在第几页。 */
  (e: 'quote', payload: { text: string; page: number }): void
}>()

type PageSlot = { number: number; el: HTMLElement | null; rendered: boolean }

const container = ref<HTMLElement | null>(null)
const pages = ref<PageSlot[]>([])
const loading = ref(false)
const failure = ref('')
const doc = shallowRef<PDFDocumentProxy | null>(null)

let lib: PdfLib | null = null
// 关文档要关加载任务，不是文档对象：worker 挂在任务上，只丢掉文档会把它留下。
let task: PDFDocumentLoadingTask | null = null
let observer: IntersectionObserver | null = null
let resizeObserver: ResizeObserver | null = null
let generation = 0

/** pdf.js 的 worker 必须在第一次取文档之前指好，否则它会去猜一个取不到的地址。 */
async function library() {
  if (lib) return lib
  const [pdfjs, workerUrl] = await Promise.all([
    import('pdfjs-dist'),
    import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
  ])
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl.default
  lib = pdfjs
  return lib
}

/** 一页按面板宽度铺满，上限 2 倍是为了不在高分屏上画出过大的位图。 */
function scaleFor(page: PDFPageProxy): number {
  const width = container.value?.clientWidth ?? 0
  const base = page.getViewport({ scale: 1 })
  if (!width || !base.width) return 1
  return Math.min((width - 32) / base.width, 2)
}

async function renderPage(slot: PageSlot, mine: number) {
  if (slot.rendered || !doc.value || !slot.el) return
  slot.rendered = true
  const page = await doc.value.getPage(slot.number)
  if (mine !== generation || !slot.el) return

  const ratio = window.devicePixelRatio || 1
  const scale = scaleFor(page)
  const viewport = page.getViewport({ scale })

  const canvas = document.createElement('canvas')
  canvas.width = Math.floor(viewport.width * ratio)
  canvas.height = Math.floor(viewport.height * ratio)
  canvas.style.width = `${Math.floor(viewport.width)}px`
  canvas.style.height = `${Math.floor(viewport.height)}px`
  slot.el.style.width = `${Math.floor(viewport.width)}px`
  slot.el.style.height = `${Math.floor(viewport.height)}px`
  slot.el.replaceChildren(canvas)

  // 交画布本身，不交 2D 上下文：v6 起 canvasContext 只为兼容保留，而两个同时给
  // 是明确不允许的。
  await page.render({
    canvas,
    viewport,
    transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
  }).promise
  if (mine !== generation || !slot.el) return

  const textLayer = document.createElement('div')
  textLayer.className = 'pv-text'
  // pdf.js 把每个 span 的 left/top 写成 `calc(… * var(--scale-factor))`，而它按
  // 自己所在元素解析这个变量。放在外层那个 div 上继承看着也对，但 pdf.js 读的是
  // 文字层本身——少了它整层会缩在左上角，而屏幕上看不出来：画布还是对的，只有
  // 选中时高亮落在别处。
  textLayer.style.setProperty('--scale-factor', String(scale))
  slot.el.appendChild(textLayer)
  const layer = new lib!.TextLayer({
    textContentSource: page.streamTextContent(),
    container: textLayer,
    viewport,
  })
  await layer.render()
}

function observe() {
  observer?.disconnect()
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        const number = Number((entry.target as HTMLElement).dataset.page)
        const slot = pages.value.find((p) => p.number === number)
        if (slot) void renderPage(slot, generation)
      }
    },
    // 提前一屏开始画，读者滚到的时候那一页已经在了。
    { root: container.value, rootMargin: '600px 0px' }
  )
  for (const slot of pages.value) if (slot.el) observer.observe(slot.el)
}

async function open(data: ArrayBuffer) {
  const mine = ++generation
  loading.value = true
  failure.value = ''
  pages.value = []
  try {
    const pdfjs = await library()
    // pdf.js 会接管这段内存，传副本进去，否则同一份字节第二次打开是空的。
    void task?.destroy()
    task = pdfjs.getDocument({ data: data.slice(0) })
    const loaded = await task.promise
    if (mine !== generation) return
    doc.value = loaded
    pages.value = Array.from({ length: loaded.numPages }, (_, i) => ({
      number: i + 1,
      el: null,
      rendered: false,
    }))
    await nextTick()
    if (mine !== generation) return
    observe()
  } catch (e) {
    if (mine !== generation) return
    failure.value = e instanceof Error ? e.message : '无法打开这个文档'
  } finally {
    if (mine === generation) loading.value = false
  }
}

/** 宽度变了就整份重画：缩放是画进位图的，拉伸会糊。
 *
 *  只作废，不补画。重新 observe 会对当下就在视野里的页立刻回调一次，由它一处
 *  发起渲染——这里再补一轮循环，两条路径会为同一页同时开工，而 `rendered` 标记
 *  是在进入时就置上的，谁先谁后取决于时序。 */
function relayout() {
  generation += 1
  for (const slot of pages.value) {
    slot.rendered = false
    slot.el?.replaceChildren()
  }
  observe()
}

let relayoutTimer: ReturnType<typeof setTimeout> | null = null
function onResize() {
  if (relayoutTimer) clearTimeout(relayoutTimer)
  relayoutTimer = setTimeout(relayout, 200)
}

function onSelect() {
  const selection = window.getSelection()
  const text = selection?.toString().trim() ?? ''
  if (!text || !selection?.rangeCount) return
  const node = selection.getRangeAt(0).startContainer
  const host = (node.nodeType === 1 ? (node as Element) : node.parentElement)?.closest('[data-page]')
  if (!host) return
  emit('quote', { text, page: Number((host as HTMLElement).dataset.page) })
}

function setRef(slot: PageSlot, el: unknown) {
  slot.el = (el as HTMLElement) ?? null
}

watch(
  () => props.data,
  (data) => {
    if (data) void open(data)
    else {
      generation += 1
      pages.value = []
    }
  },
  { immediate: true }
)

watch(container, (el) => {
  resizeObserver?.disconnect()
  if (!el) return
  resizeObserver = new ResizeObserver(onResize)
  resizeObserver.observe(el)
})

onBeforeUnmount(() => {
  generation += 1
  observer?.disconnect()
  resizeObserver?.disconnect()
  if (relayoutTimer) clearTimeout(relayoutTimer)
  void task?.destroy()
})
</script>

<template>
  <div ref="container" class="pv" @mouseup="onSelect">
    <div v-if="loading" class="pv__state">
      <v-progress-circular indeterminate color="primary" size="24" />
    </div>
    <div v-else-if="failure" class="pv__state pv__state--text">
      <v-icon size="28" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <div>无法显示这个文档</div>
      <div class="t-meta mt-1">{{ failure }}</div>
    </div>
    <div
      v-for="slot in pages"
      :key="slot.number"
      :ref="(el) => setRef(slot, el)"
      :data-page="slot.number"
      class="pv__page"
    />
  </div>
</template>

<style scoped>
.pv {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  background: var(--canvas);
}

.pv__state {
  padding: 32px 16px;
  color: var(--muted);
}
.pv__state--text {
  text-align: center;
}

/* 纸只描边，不投影——层次由 --surface 与 --canvas 的明暗差表达。 */
.pv__page {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  overflow: hidden;
  flex: none;
}

.pv__page :deep(canvas) {
  display: block;
}

/* 透明的可选文字，盖在画布上。定位由 pdf.js 按 --scale-factor 写在每个 span 上。 */
.pv__page :deep(.pv-text) {
  position: absolute;
  inset: 0;
  overflow: hidden;
  line-height: 1;
  text-size-adjust: none;
  forced-color-adjust: none;
  transform-origin: 0 0;
}

.pv__page :deep(.pv-text span),
.pv__page :deep(.pv-text br) {
  position: absolute;
  white-space: pre;
  color: transparent;
  cursor: text;
  transform-origin: 0 0;
}

.pv__page :deep(.pv-text ::selection) {
  background: var(--selection-bg);
}
</style>
