<script setup lang="ts">
// 分页文档的阅读视图：PDF 本身，以及 Word、幻灯片转换成 PDF 之后的样子。
//
// 画布之上盖一层透明的文字层，这是这个组件唯一非显然的地方，也是它存在的理由。
// 没有它，一页文档就是一张图：读的人不能选、不能搜、不能复制一句话给芝士看。
// 有了它，读者指着一句话说「这里不对」这件事才成立——选中的原文就是交给芝士的坐标。
//
// 指不完的地方就指位置：图里的东西、排版的空当，那些没有文字可选的地方，「指位置」
// 让读者在页面上点一下，交出去的是这一页的哪个比例位置。
//
// 页面按需渲染。一份几十页的文档一次性全画出来，等待的是空白，而且大多数页永远
// 不会被看到。

import type { PDFDocumentLoadingTask, PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist'
import type { PagePin, SlideSource } from './slidesContext'

import { nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

type PdfLib = typeof import('pdfjs-dist/legacy/build/pdf.mjs')

const props = defineProps<{
  /** 文档的原始字节。换一份文档就换一个 ArrayBuffer。 */
  data: ArrayBuffer | null
  /** 这一份文档已验证的身份。没有它就不让指位置：指出去的一定带版本，指着一个
   *  自己都说不上是哪一版的页面，受话人没法知道他说的是哪一份。 */
  context?: SlideSource
}>()

const emit = defineEmits<{
  /** 读者选中了一段原文，附带它在第几页。 */
  (e: 'quote', payload: { text: string; page: number }): void
  /** 读者在一页上点了一下。 */
  (e: 'pin', payload: PagePin): void
  /** 屏上那一点被撤掉了（重排把它撤了），外面记着的那一点也跟着作废。 */
  (e: 'dropped'): void
}>()

/** `host` 是页里那块画布住的地方，和 `el` 分开是因为重画要 `replaceChildren`——
 *  清掉的只能是画出来的那一层，页上那点标记得留着。 */
type PageSlot = {
  number: number
  el: HTMLElement | null
  host: HTMLElement | null
  rendered: boolean
}

const container = ref<HTMLElement | null>(null)
const pages = ref<PageSlot[]>([])
const loading = ref(false)
const failure = ref('')
const doc = shallowRef<PDFDocumentProxy | null>(null)
/** 指位置模式。开着的时候点页面是「指这里」，不是选文字。 */
const pointing = ref(false)
/** 刚指过的那一点，画在页上，读者看得见自己指的是哪儿。 */
const marked = ref<{ page: number; x: number; y: number } | null>(null)

let lib: PdfLib | null = null
// 关文档要关加载任务，不是文档对象：worker 挂在任务上，只丢掉文档会把它留下。
let task: PDFDocumentLoadingTask | null = null
let observer: IntersectionObserver | null = null
let resizeObserver: ResizeObserver | null = null
// 两份「代际」是两件事，别合成一个：`loadGeneration` 管的是「这一份文档的加载还
// 算不算数」，`renderGeneration` 管的是「已经画出来的页还算不算数」。它们共用一个
// 计数器时，重排会把加载一起作废掉——挂载时 ResizeObserver 的那次初回调 200 毫秒
// 后就触发一次重排，而 pdf.js 解析一份文档要一秒多，于是 open() 每次都在
// `await task.promise` 之后发现自己已经过期、空手返回；`finally` 又只在代际没变时
// 才关掉 loading，预览就永远转圈，既不报错也不超时。
let loadGeneration = 0
let renderGeneration = 0

/** pdf.js 的 worker 必须在第一次取文档之前指好，否则它会去猜一个取不到的地址。 */
async function library() {
  if (lib) return lib
  const [pdfjs, workerUrl] = await Promise.all([
    import('pdfjs-dist/legacy/build/pdf.mjs'),
    import('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'),
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
  if (slot.rendered || !doc.value || !slot.el || !slot.host) return
  slot.rendered = true
  const host = slot.host
  try {
    const page = await doc.value.getPage(slot.number)
    if (mine !== renderGeneration || !slot.el || !slot.host) return

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
    host.replaceChildren(canvas)

    // 交画布本身，不交 2D 上下文：v6 起 canvasContext 只为兼容保留，而两个同时给
    // 是明确不允许的。
    await page.render({
      canvas,
      viewport,
      transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
    }).promise
    if (mine !== renderGeneration || !slot.el || !slot.host) return

    const textLayer = document.createElement('div')
    textLayer.className = 'pv-text'
    // pdf.js 把每个 span 的 left/top 写成 `calc(… * var(--scale-factor))`，而它按
    // 自己所在元素解析这个变量。放在外层那个 div 上继承看着也对，但 pdf.js 读的是
    // 文字层本身——少了它整层会缩在左上角，而屏幕上看不出来：画布还是对的，只有
    // 选中时高亮落在别处。
    textLayer.style.setProperty('--scale-factor', String(scale))
    host.appendChild(textLayer)
    const layer = new lib!.TextLayer({
      textContentSource: page.streamTextContent(),
      container: textLayer,
      viewport,
    })
    await layer.render()
  } catch (e) {
    if (mine !== renderGeneration || !slot.host) return
    showPageFailure(slot, e)
  }
}

/** 一页画不出来时，就在那一页的位置说清楚是哪一页、为什么。
 *
 *  不这么做的话，屏幕上是永远的空白——和这个组件最初那个 bug 是同一副样子：读者
 *  分不出「还在画」「这一页没有内容」和「坏了」，也没有东西可点。别的页照旧画。 */
function showPageFailure(slot: PageSlot, e: unknown) {
  if (!slot.host) return
  const box = document.createElement('div')
  box.className = 'pv-error'
  box.textContent = t('work.room.preview.pageFailed', {
    page: slot.number,
    error: e instanceof Error ? e.message : t('work.room.preview.renderFailed'),
  })
  slot.host.replaceChildren(box)
}

function observe() {
  observer?.disconnect()
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        const number = Number((entry.target as HTMLElement).dataset.page)
        const slot = pages.value.find((p) => p.number === number)
        if (slot) void renderPage(slot, renderGeneration)
      }
    },
    // 提前一屏开始画，读者滚到的时候那一页已经在了。
    { root: container.value, rootMargin: '600px 0px' }
  )
  for (const slot of pages.value) if (slot.el) observer.observe(slot.el)
}

async function open(data: ArrayBuffer) {
  const mine = ++loadGeneration
  // 换了一份文档，上一份画出来的页就不算数了。
  renderGeneration += 1
  loading.value = true
  failure.value = ''
  pages.value = []
  marked.value = null
  try {
    const pdfjs = await library()
    // pdf.js 会接管这段内存，传副本进去，否则同一份字节第二次打开是空的。
    void task?.destroy()
    task = pdfjs.getDocument({ data: data.slice(0) })
    const loaded = await task.promise
    if (mine !== loadGeneration) return
    doc.value = loaded
    pages.value = Array.from({ length: loaded.numPages }, (_, i) => ({
      number: i + 1,
      el: null,
      host: null,
      rendered: false,
    }))
    await nextTick()
    if (mine !== loadGeneration) return
    observe()
  } catch (e) {
    if (mine !== loadGeneration) return
    failure.value = e instanceof Error ? e.message : t('work.room.preview.docOpenFailed')
  } finally {
    if (mine === loadGeneration) loading.value = false
  }
}

/** 宽度变了就整份重画：缩放是画进位图的，拉伸会糊。
 *
 *  只作废，不补画。重新 observe 会对当下就在视野里的页立刻回调一次，由它一处
 *  发起渲染——这里再补一轮循环，两条路径会为同一页同时开工，而 `rendered` 标记
 *  是在进入时就置上的，谁先谁后取决于时序。
 *
 *  只推进 `renderGeneration`：重排作废的是已经画出来的页，跟「哪一份文档正在加载」
 *  无关。它推错了计数器（推了加载那一个）的后果，就是 ResizeObserver 挂载时的初
 *  回调把一份还在解析的文档整个丢掉——见上面两份代际的说明。 */
function relayout() {
  renderGeneration += 1
  // 页要重画，指过的那一点也得撤：它是按比例画在页上的，页面尺寸一变，同一个比例
  // 落在别的内容上，读者看到的就不再是他指的那一处。撤了要告诉外面——外面还握着
  // 那一点的副本，不撤的话读者能发出一条屏上已经没有的指认。
  if (marked.value) {
    marked.value = null
    emit('dropped')
  }
  for (const slot of pages.value) {
    slot.rendered = false
    slot.host?.replaceChildren()
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
  // 和 `PreviewSlides.quote()`、父级 `onQuote` 同一条门槛：先归一化空白，再要求不止
  // 一个字。只 trim 的话，一个字符会一路发到父级、在那里被丢掉——读者看到的是「选了
  // 没反应」，而这正是那句门槛存在的理由。
  const text = selection?.toString().replace(/\s+/g, ' ').trim() ?? ''
  if (text.length < 2 || !selection?.rangeCount) return
  const node = selection.getRangeAt(0).startContainer
  const host = (node.nodeType === 1 ? (node as Element) : node.parentElement)?.closest('[data-page]')
  if (!host) return
  emit('quote', { text, page: Number((host as HTMLElement).dataset.page) })
}

function setRef(slot: PageSlot, el: unknown) {
  slot.el = (el as HTMLElement) ?? null
}

function setHost(slot: PageSlot, el: unknown) {
  slot.host = (el as HTMLElement) ?? null
}

function togglePointing() {
  pointing.value = !pointing.value
  if (!pointing.value) marked.value = null
}

function ratio(value: number): number {
  return Math.min(1, Math.max(0, value))
}

/** 点了一下：算出它在那一页的哪个比例位置。
 *
 *  没画出来的页不接受——页框先摆在那儿，可它的尺寸还是零，点上去算出来的比例没有
 *  意义，而读者看不见自己指了什么。 */
function onPoint(event: MouseEvent) {
  if (!pointing.value || !props.context) return
  const target = event.target
  const pageEl = (target instanceof Element ? target : null)?.closest('[data-page]') as HTMLElement | null
  if (!pageEl || !container.value?.contains(pageEl)) return
  const slot = pages.value.find((p) => p.el === pageEl)
  if (!slot) return
  const canvas = slot.host?.querySelector('canvas')
  if (!canvas) return
  // 量画布，不量页框：页框那一圈 1px 描边不算纸面，而 `getBoundingClientRect` 给的
  // 正是含描边的外框——拿它当原点和分母，落点会整体偏出约两个像素。标记本身按
  // 百分比定位在内边距框里，也就是画布那一块，两边量的得是同一个框。
  const box = canvas.getBoundingClientRect()
  if (!box.width || !box.height) return
  const x = ratio((event.clientX - box.left) / box.width)
  const y = ratio((event.clientY - box.top) / box.height)
  marked.value = { page: slot.number, x, y }
  emit('pin', { page: slot.number, x, y, context: props.context })
}

/** 拿这一页现在画出来的样子换一张 PNG，交给指出的那一处当配图。
 *
 *  原尺寸交出去太大：高分屏上一页能到三千像素宽，而附件是给受话人看的。等比缩到
 *  这个宽度以内，比例和落点是同一套，看的人拿它对照页上的位置不会错位。
 *
 *  没有画布（页面还没画出来）或浏览器不给 PNG 时返回 null——那时只是没有配图，
 *  位置那句话照样成立。 */
const SNAPSHOT_MAX_WIDTH = 1600
async function snapshot(page: number): Promise<{ blob: Blob; width: number; height: number } | null> {
  const canvas = pages.value.find((p) => p.number === page)?.host?.querySelector('canvas')
  if (!canvas) return null
  try {
    let source: HTMLCanvasElement = canvas
    if (canvas.width > SNAPSHOT_MAX_WIDTH) {
      const scaled = document.createElement('canvas')
      scaled.width = SNAPSHOT_MAX_WIDTH
      scaled.height = Math.max(1, Math.round((canvas.height * SNAPSHOT_MAX_WIDTH) / canvas.width))
      const ctx = scaled.getContext('2d')
      if (!ctx) return null
      ctx.drawImage(canvas, 0, 0, scaled.width, scaled.height)
      source = scaled
    }
    const blob = await new Promise<Blob | null>((resolve) => source.toBlob((b) => resolve(b), 'image/png'))
    if (!blob) return null
    return { blob, width: source.width, height: source.height }
  } catch {
    // 这个浏览器给不出 PNG（画布被污染、toBlob 缺失）：位置那句话照样发得出去，
    // 只是没有配图。
    return null
  }
}

/** 抹掉指过的那一点，并且退出指位置：这一点已经交出去了，或者它依据的东西没了，
 *  屏上就不该再留着一个记号让读者以为还能再发一次。 */
function clearMark() {
  marked.value = null
  pointing.value = false
}
defineExpose({ snapshot, clearMark })

watch(
  () => props.data,
  (data) => {
    if (data) void open(data)
    else {
      loadGeneration += 1
      renderGeneration += 1
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
  loadGeneration += 1
  renderGeneration += 1
  observer?.disconnect()
  resizeObserver?.disconnect()
  if (relayoutTimer) clearTimeout(relayoutTimer)
  void task?.destroy()
})
</script>

<template>
  <div ref="container" class="pv" :class="{ 'pv--pointing': pointing }" @mouseup="onSelect" @click="onPoint">
    <div class="pv__tools">
      <BaseButton
        size="sm"
        :kind="pointing ? 'primary' : 'ghost'"
        :prepend-icon="pointing ? 'mdi-crosshairs-gps' : 'mdi-crosshairs'"
        :aria-pressed="pointing"
        :disabled="!context"
        :title="context ? t('work.room.preview.pinHint') : t('work.room.preview.pinNeedsVersion')"
        @click.stop="togglePointing"
      >
        {{ t('work.room.preview.pin') }}
      </BaseButton>
    </div>
    <div v-if="loading" class="pv__state">
      <v-progress-circular indeterminate color="primary" size="24" />
    </div>
    <div v-else-if="failure" class="pv__state pv__state--text">
      <v-icon size="28" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <div>{{ t('work.room.preview.docCantDisplay') }}</div>
      <div class="t-meta mt-1">{{ failure }}</div>
    </div>
    <div
      v-for="slot in pages"
      :key="slot.number"
      :ref="(el) => setRef(slot, el)"
      :data-page="slot.number"
      class="pv__page"
    >
      <div :ref="(el) => setHost(slot, el)" class="pv__canvas" />
      <!-- 指过的那一点。画在页里，不画在容器上：页是按比例定位的，滚动和宽度变化
           都跟着它走。 -->
      <span
        v-if="marked && marked.page === slot.number"
        class="pv__pin"
        :style="{ left: `${marked.x * 100}%`, top: `${marked.y * 100}%` }"
        aria-hidden="true"
      />
    </div>
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

/* 一页画不出来时占着那一页的位置，替掉那张纸。 */
.pv-error {
  padding: 32px 16px;
  color: var(--muted);
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

/* 指位置的时候点的是纸，不是纸上的字：文字层整个让开，否则读者一点就选中一行字。 */
.pv--pointing .pv__page {
  cursor: crosshair;
}
.pv--pointing .pv__page :deep(.pv-text) {
  pointer-events: none;
}

/* 页在上、工具条在右上：滚到哪儿它都在。 */
.pv__tools {
  position: sticky;
  top: 0;
  align-self: flex-end;
  z-index: 2;
  display: flex;
  gap: 8px;
  margin-bottom: -8px;
  padding-bottom: 8px;
}

.pv__canvas {
  position: absolute;
  inset: 0;
}

.pv__pin {
  position: absolute;
  width: 14px;
  height: 14px;
  margin: -7px 0 0 -7px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 2px var(--surface);
  pointer-events: none;
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
