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
//
// 手机上一页按页宽铺开，正文只有六七个像素：两根手指捏着放大，点两下放大到正文那一栏
// 刚好占满宽度、再点两下回到整页。缩放先只把已经画好的页拉大（立刻跟手），手停下来再按
// 新的比例重画一遍，免得一路是糊的。

import type { PDFDocumentLoadingTask, PDFDocumentProxy } from 'pdfjs-dist'
import type { PagePin, SlidePageContext, SlideSource } from './slidesContext'

import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'

import { usePinchZoom } from '@/composables/usePinchZoom'

import { contextAround } from './markdownQuote'
import { useSelectionHighlight } from './selectionHighlight'
import { pageRatio } from './slidesContext'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { usePreviewNarrow } from '@/lib/previewNarrow'

type PdfLib = typeof import('pdfjs-dist/legacy/build/pdf.mjs')

const props = defineProps<{
  /** 文档的原始字节。换一份文档就换一个 ArrayBuffer。 */
  data: ArrayBuffer | null
  /** 这一份文档已验证的身份。没有它就不让指位置：指出去的一定带版本，指着一个
   *  自己都说不上是哪一版的页面，受话人没法知道他说的是哪一份。 */
  context?: SlideSource
  /** 这一份文件上已有的评论指着的那几处：第几页、页内比例（`page-pin` 的形状）。
   *  页码在这一版里落不到任何一页上时自然不画（页数变了）。 */
  marks?: readonly { id: string; page: number; x: number; y: number }[]
  /** 现在打开的是哪一条：它那一处画得显眼些。 */
  activeMark?: string | null
}>()

const emit = defineEmits<{
  /** 读者选中了一段原文，附带它在第几页。指的是哪一版只有这一句，没有文件身份——
   *  那是「没有已验证的身份」时那条老路（`pageContext` 才带身份）。 */
  (e: 'quote', payload: { text: string; page: number }): void
  /** 读者选中的一段，连同这一份已验证的身份：和幻灯片选段走同一条出口。
   *
   *  两者本是一回事——`slide-page` 这个锚点说的就是「某份文档的一页，整页或页里的
   *  一段」，`scope` 分这两样。分页这一档自己核过字节、身份最靠得住，却一直只发一
   *  句不带版本的纯文本，受话人回到的那一版可能早不是读者指的那一版。 */
  (e: 'page-context', payload: SlidePageContext): void
  /** 读者在一页上点了一下。 */
  (e: 'pin', payload: PagePin): void
  /** 屏上那一点被撤掉了（重排把它撤了），外面记着的那一点也跟着作废。 */
  (e: 'dropped'): void
  /** 读者点了页上某一颗评论标记：把那一串评论打开。 */
  (e: 'mark', id: string): void
}>()

/** `host` 是页里那块画布住的地方，和 `el` 分开是因为重画要 `replaceChildren`——
 *  清掉的只能是画出来的那一层，页上那点标记得留着。 */
type PageSlot = {
  number: number
  el: HTMLElement | null
  host: HTMLElement | null
  rendered: boolean
  /** 这一页在 1 倍下的大小（PDF 的点）。还没取到这一页时先借第一页的：绝大多数文档每页
   *  一样大，页框先按它占好位置，缩放时的落点才算得准。 */
  base: { width: number; height: number } | null
}

/** 这一页上被评论指着的那些地方。按页分好一次，别在模板里每页筛一遍整个列表
 *  ——页多的时候那是「页数 × 标记数」，而它每次重渲染都跑。 */
const marksByPage = computed(() => {
  const byPage = new Map<number, { id: string; x: number; y: number }[]>()
  for (const mark of props.marks ?? []) {
    const on = byPage.get(mark.page)
    if (on) on.push(mark)
    else byPage.set(mark.page, [mark])
  }
  return byPage
})

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

const { narrow } = usePreviewNarrow(container)
/** 页四周留的空：窄处收到一半，省下的都给正文。和样式里 `.pv--narrow` 的内边距是同一个数。 */
const gutter = computed(() => (narrow.value ? 8 : 16))

/** 读者捏出来的放大倍数，1 是「一页刚好铺满宽度」。 */
const zoom = ref(1)
const MIN_ZOOM = 1
const MAX_ZOOM = 4
/** 一张画布最多这么多像素。iOS 上超过约 1600 万像素的画布直接画不出来（一片空白），放大到
 *  四倍的一页在三倍屏上会越过它，所以位图精度在这里封顶，宁可略糊。 */
const MAX_CANVAS_PIXELS = 16_000_000

/** 一页按面板宽度铺满，上限 2 倍是为了不在宽屏上把一页放得比纸还大；再乘上读者捏出来的倍数。 */
function scaleOf(base: { width: number }): number {
  const width = container.value?.clientWidth ?? 0
  if (!width || !base.width) return zoom.value
  return Math.min((width - 2 * gutter.value) / base.width, 2) * zoom.value
}

/** 按现在的比例摆好这一页的框。已经画好的画布和文字层跟着拉伸（画布铺满页框，文字层的
 *  几何全由 `--total-scale-factor` 乘出来），重画之前选字、标记的位置照样对得上。 */
function sizeSlot(slot: PageSlot) {
  if (!slot.el || !slot.base) return
  const scale = scaleOf(slot.base)
  slot.el.style.width = `${Math.floor(slot.base.width * scale)}px`
  slot.el.style.height = `${Math.floor(slot.base.height * scale)}px`
  slot.host?.querySelector<HTMLElement>('.pv-text')?.style.setProperty('--total-scale-factor', String(scale))
}

async function renderPage(slot: PageSlot, mine: number) {
  if (slot.rendered || !doc.value || !slot.el || !slot.host) return
  slot.rendered = true
  const host = slot.host
  try {
    const page = await doc.value.getPage(slot.number)
    if (mine !== renderGeneration || !slot.el || !slot.host) return

    const base = page.getViewport({ scale: 1 })
    slot.base = { width: base.width, height: base.height }
    const scale = scaleOf(slot.base)
    const viewport = page.getViewport({ scale })
    const ratio = Math.min(
      window.devicePixelRatio || 1,
      Math.sqrt(MAX_CANVAS_PIXELS / Math.max(1, viewport.width * viewport.height))
    )

    const canvas = document.createElement('canvas')
    canvas.width = Math.floor(viewport.width * ratio)
    canvas.height = Math.floor(viewport.height * ratio)
    sizeSlot(slot)

    // 先在画面外画好再换上去：缩放之后重画的那一页，换上之前读者看着的是拉大的旧图，
    // 不是一块白。
    //
    // 交画布本身，不交 2D 上下文：v6 起 canvasContext 只为兼容保留，而两个同时给
    // 是明确不允许的。
    await page.render({
      canvas,
      viewport,
      transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
    }).promise
    if (mine !== renderGeneration || !slot.el || !slot.host) return
    host.replaceChildren(canvas)

    const textLayer = document.createElement('div')
    textLayer.className = 'pv-text'
    // 文字层的几何由 pdf.js 写成变量，样式负责把变量乘出来，而这套变量它按自己所在
    // 的元素读——所以三个都得挂在文字层上。`--total-scale-factor` 它自己就要用：文字
    // 层的宽高是它按这个变量算出来写在元素上的。
    textLayer.style.setProperty('--total-scale-factor', String(scale))
    textLayer.style.setProperty('--scale-round-x', '1px')
    textLayer.style.setProperty('--scale-round-y', '1px')
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
    const first = (await loaded.getPage(1)).getViewport({ scale: 1 })
    if (mine !== loadGeneration) return
    zoom.value = 1
    pages.value = Array.from({ length: loaded.numPages }, (_, i) => ({
      number: i + 1,
      el: null,
      host: null,
      rendered: false,
      base: { width: first.width, height: first.height },
    }))
    await nextTick()
    if (mine !== loadGeneration) return
    for (const slot of pages.value) sizeSlot(slot)
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
    sizeSlot(slot)
  }
  observe()
}

// ---- 缩放 ----

let redrawTimer: ReturnType<typeof setTimeout> | null = null
/** 手停下来以后按新的比例重画。只作废、不清空：拉大的旧图留在那儿，新图画好了再换。
 *  指过的那一点不撤——缩放是整页等比的，同一个比例落在同一处内容上（和改宽度时重排不同）。 */
function redrawSoon() {
  if (redrawTimer) clearTimeout(redrawTimer)
  redrawTimer = setTimeout(() => {
    redrawTimer = null
    renderGeneration += 1
    for (const slot of pages.value) slot.rendered = false
    observe()
  }, 150)
}

/** 屏幕上这一点落在哪一页的哪个比例上。 */
function pageAt(x: number, y: number) {
  for (const slot of pages.value) {
    const box = slot.el?.getBoundingClientRect()
    if (box && box.width && y >= box.top && y <= box.bottom) {
      return { slot, rx: (x - box.left) / box.width, ry: (y - box.top) / box.height }
    }
  }
  return null
}

/** 缩放到 `next`，并让 `anchor` 那一处内容留在屏幕上 `to` 这一点（默认就是它原来的位置）。 */
function zoomTo(
  next: number,
  at: { x: number; y: number },
  anchor = pageAt(at.x, at.y),
  to: { x: number; y: number } = at
) {
  const host = container.value
  if (!host) return
  zoom.value = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next))
  for (const slot of pages.value) sizeSlot(slot)
  const box = anchor?.slot.el?.getBoundingClientRect()
  if (anchor && box) {
    host.scrollLeft += box.left + anchor.rx * box.width - to.x
    host.scrollTop += box.top + anchor.ry * box.height - to.y
  }
  redrawSoon()
}

/** 点两下：放大到手指底下那一栏正文刚好占满宽度；已经放大了就回到整页。
 *
 *  「那一栏」从文字层量：手指附近一屏高之内的那些字，从离手指最近的一段起，横向上和它
 *  搭得上的都算进来。双栏的论文点哪一栏放大哪一栏；一页没有文字（扫描件）就按两倍放大。 */
function onDoubleTap(point: { x: number; y: number }) {
  const host = container.value
  if (!host) return
  if (zoom.value > 1.05) {
    zoomTo(1, point)
    return
  }
  const anchor = pageAt(point.x, point.y)
  const column = anchor && columnAt(anchor.slot, point, host.clientHeight)
  const page = anchor?.slot.el?.getBoundingClientRect()
  if (!anchor || !column || !page) {
    zoomTo(2, point)
    return
  }
  const room = host.clientWidth - 2 * gutter.value
  const next = (zoom.value * room) / (column.right - column.left)
  if (next < 1.15) {
    zoomTo(2, point)
    return
  }
  // 那一栏的左边贴住留白的内沿，手指那一行留在原来的高度。
  const left = { slot: anchor.slot, rx: (column.left - page.left) / page.width, ry: anchor.ry }
  zoomTo(next, point, left, { x: host.getBoundingClientRect().left + gutter.value, y: point.y })
}

function columnAt(slot: PageSlot, point: { x: number; y: number }, reach: number) {
  const boxes = [...(slot.host?.querySelectorAll<HTMLElement>('.pv-text span') ?? [])]
    .filter((span) => (span.textContent ?? '').trim())
    .map((span) => span.getBoundingClientRect())
    .filter((box) => box.width > 0 && Math.abs(box.top + box.height / 2 - point.y) < reach)
  if (!boxes.length) return null
  const distance = (box: DOMRect) =>
    Math.hypot(
      Math.max(box.left - point.x, 0, point.x - box.right),
      Math.max(box.top - point.y, 0, point.y - box.bottom)
    )
  const nearest = boxes.reduce((best, box) => (distance(box) < distance(best) ? box : best))
  let left = nearest.left
  let right = nearest.right
  // 两遍：第一遍并进来的段可能让第二遍搭上更远的那几段。
  for (let pass = 0; pass < 2; pass += 1) {
    for (const box of boxes) {
      if (box.right > left && box.left < right) {
        left = Math.min(left, box.left)
        right = Math.max(right, box.right)
      }
    }
  }
  return right - left > 0 ? { left, right } : null
}

let pinchAnchor: ReturnType<typeof pageAt> = null
usePinchZoom(container, {
  zoom: () => zoom.value,
  min: MIN_ZOOM,
  max: MAX_ZOOM,
  enabled: () => !pointing.value && pages.value.length > 0,
  // 钉住的是两指刚放下时中点底下那一处，之后两指一起挪，页跟着挪：捏着就能平移。
  onZoom: (next, origin, phase) => {
    if (phase === 'start' || !pinchAnchor) pinchAnchor = pageAt(origin.x, origin.y)
    zoomTo(next, origin, pinchAnchor)
    if (phase === 'end') pinchAnchor = null
  },
  onDoubleTap,
})

let relayoutTimer: ReturnType<typeof setTimeout> | null = null
function onResize() {
  if (relayoutTimer) clearTimeout(relayoutTimer)
  relayoutTimer = setTimeout(relayout, 200)
}

// 选中那段文字的底色自己画。浏览器给每一段文字各画一层，而文字层里相邻两段是搭着
// 的，叠出来的那块比别处深——读者看到的就是「字旁边多出来一个框」。见那个文件。
useSelectionHighlight({
  sheets: () => pages.value.map((slot) => slot.el).filter((el): el is HTMLElement => !!el),
  layerClass: 'pv-text',
  paintClass: 'pv-hl',
})

/** 这一页现在能不能带身份地指出去：画出来了、这一份是屏幕上那一版、也没有转不动。
 *
 *  和幻灯片那边（`PreviewSlides.canQuote`）同一条判据——那边也只在 `ready` 上建。
 *  松这一档没有意义：真发出去之前父级还会把身份整个再核一遍（`usePreviewLocator`
 *  走 `usePreviewPagePin` 的 `canUse`），这里放行的只会是一场白选。 */
const canQuote = computed(
  () => !!props.context && pages.value.some((slot) => slot.rendered) && !loading.value && !failure.value
)

function onSelect() {
  const selection = window.getSelection()
  // 和 `PreviewSlides.quote()`、父级 `onQuote` 同一条门槛：先归一化空白，再要求不止
  // 一个字。只 trim 的话，一个字符会一路发到父级、在那里被丢掉——读者看到的是「选了
  // 没反应」，而这正是那句门槛存在的理由。
  const text = selection?.toString().replace(/\s+/g, ' ').trim() ?? ''
  if (text.length < 2 || !selection?.rangeCount) return
  const range = selection.getRangeAt(0)
  const node = range.startContainer
  const host = (node.nodeType === 1 ? (node as Element) : node.parentElement)?.closest('[data-page]')
  if (!host) return
  const page = Number((host as HTMLElement).dataset.page)
  // 两侧文字量的是这一页的文字层，而且要求**两头**都落在里面。只核一头是不够的：
  // 拖过页脚的选区起在这一页的文字层、结在下一页的，`contextAround` 把结尾挪到起点
  // 之后 `suffix` 就成了空串——交出去的是「这是第 2 页的一段」，而那段话其实跨到了
  // 第 3 页。幻灯片那边也是两头都核（`sheet.contains(start) && sheet.contains(end)`）。
  //
  // 量不到就**不带**两侧文字：它们在锚点里本来就是可选的（`SlidePageContext.prefix`
  // 是 undefined 时序列化连键都不出现），比量出一段错的、或者拿页框当根把这一页的
  // 错误提示和评论按钮当成正文量进去要好。
  const startEl = node.nodeType === 1 ? (node as Element) : node.parentElement
  const textLayer = startEl?.closest<HTMLElement>('.pv-text') ?? null
  const inOneLayer = !!textLayer && textLayer.contains(range.endContainer)
  // 这一段的左上角在页上的比例位置：攒着一起发的那几处要靠它在页上画序号。
  // 量画布不量页框（理由同 `onPoint`：1px 描边不算纸面）；量不到就不带。
  const canvas = (host as HTMLElement).querySelector('canvas')
  const canvasBox = canvas?.getBoundingClientRect()
  const rect = range.getBoundingClientRect()
  const spot =
    canvasBox && canvasBox.width > 0 && canvasBox.height > 0
      ? {
          x: pageRatio((rect.left - canvasBox.left) / canvasBox.width),
          y: pageRatio((rect.top - canvasBox.top) / canvasBox.height),
        }
      : {}
  // 有身份就走幻灯片那条结构化出口：同一个锚点、同一份版本，两侧文字帮受话人分辨同
  // 一句话在这一页的哪一处。没有身份（这一份还没核过）才退回那句不带版本的拼话——
  // 它指不回哪一版，但也不撒谎。
  if (canQuote.value && props.context) {
    emit('page-context', {
      text,
      page,
      scope: 'selection',
      context: { ...props.context },
      ...(inOneLayer && textLayer ? contextAround(textLayer, range) : {}),
      ...spot,
    })
    return
  }
  emit('quote', { text, page })
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
  const x = pageRatio((event.clientX - box.left) / box.width)
  const y = pageRatio((event.clientY - box.top) / box.height)
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
  if (redrawTimer) clearTimeout(redrawTimer)
  void task?.destroy()
})
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- pointer gestures over the pages: a click drops a pin in pointing mode and a mouse selection is quoted; neither has a keyboard counterpart yet -->
  <div
    ref="container"
    class="pv"
    :class="{ 'pv--pointing': pointing, 'pv--narrow': narrow }"
    @mouseup="onSelect"
    @click="onPoint"
  >
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
      <!-- Comment marks: one per thread whose page-pin lands on this page. Drawn inside
           the page box, not on the container, so they follow the page as it is scaled
           and scrolled. They take the pointer, because clicking one opens the rail. -->
      <button
        v-for="mark in marksByPage.get(slot.number) ?? []"
        :key="mark.id"
        type="button"
        class="pv__cmt"
        :class="{ 'is-active': mark.id === activeMark }"
        :data-comment-mark="mark.id"
        :style="{ left: `${mark.x * 100}%`, top: `${mark.y * 100}%` }"
        :aria-label="t('work.room.comments.commentHere')"
        :title="t('work.room.comments.commentHere')"
        @click.stop="emit('mark', mark.id)"
      />
      <!-- The spot the reader just pointed at. -->
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
  /* 页靠 `margin-inline: auto` 居中，不靠 align-items：放大到比这一格还宽时，居中会把左边
     那一截推到滚不到的地方，自动外边距在放不下时归零、多出来的都在右边，滚得到。 */
  align-items: flex-start;
  gap: 16px;
  background: var(--canvas);
  /* 单指照常滚（横竖都行，放大以后要横着看），浏览器自己的整页缩放和双击放大让给这一格。 */
  touch-action: pan-x pan-y;
}
.pv--narrow {
  padding: 8px;
  gap: 8px;
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
  margin-inline: auto;
}

/* 指位置的时候点的是纸，不是纸上的字：文字层整个让开，否则读者一点就选中一行字。 */
.pv--pointing .pv__page {
  cursor: crosshair;
}
.pv--pointing .pv__page :deep(.pv-text) {
  pointer-events: none;
}

/* 工具条浮在内容右上角，不占行高：容器高度归零、overflow 放出来，按钮自己带着不透明
   的底和一层轻影，压在纸上仍然读得清。sticky 不变，滚到哪儿它都在。 */
.pv__tools {
  position: sticky;
  top: 0;
  /* 横向也钉住：放大以后横着滚，按钮仍贴在看得见的那一块的右上角，不跟着纸跑。 */
  left: 0;
  align-self: stretch;
  justify-content: flex-end;
  z-index: var(--z-raised-2);
  display: flex;
  gap: 8px;
  height: 0;
  overflow: visible;
}
.pv__tools :deep(.base-btn) {
  background: var(--surface);
  box-shadow: var(--shadow-1);
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

/* 有人在页上评过的一处：一颗琥珀的小点，点得到（评论栏靠它打开）。比「刚指的那一点」
   小一圈、空心，一眼能分清「这是我的指认」和「这是别人留下的评论」。 */
.pv__cmt {
  position: absolute;
  width: 12px;
  height: 12px;
  margin: -6px 0 0 -6px;
  padding: 0;
  border: 2px solid var(--accent);
  border-radius: 50%;
  background: var(--surface);
  cursor: pointer;
  transition: background var(--dur-quick) var(--ease-standard);
}
.pv__cmt:hover,
.pv__cmt.is-active {
  background: var(--accent);
}

.pv__page :deep(canvas) {
  display: block;
  width: 100%;
  height: 100%;
}

/* 透明的可选文字，盖在画布上。
 *
 * pdf.js 6 只把几何写成变量：每个 span 一个 `--font-height`（这一行字的原始高矮，
 * 不含缩放）、长句子一个 `--scale-x`、竖排一段 `--rotate`，而字号和 transform 得由
 * 样式从这里算出来。少了这一段，字还是透明的、位置也对，坏在看不见的地方：每个
 * span 按继承来的字号排版，盒子大小和纸上那些字对不上，选中落在隔壁，高亮盖不住。 */
.pv__page :deep(.pv-text) {
  --min-font-size: 1;
  --text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
  --min-font-size-inv: calc(1 / var(--min-font-size));
  position: absolute;
  text-align: initial;
  inset: 0;
  /* `clip` 不是 `hidden`：hidden 是一个能滚的框，一路拖下去选的时候浏览器会把它自己
     滚走，高亮跟着离开纸面，再被页框裁掉。 */
  overflow: clip;
  line-height: 1;
  letter-spacing: normal;
  word-spacing: normal;
  text-size-adjust: none;
  forced-color-adjust: none;
  transform-origin: 0 0;
  caret-color: CanvasText;
  z-index: 0;
}

.pv__page :deep(.pv-text span),
.pv__page :deep(.pv-text br) {
  position: absolute;
  white-space: pre;
  color: transparent;
  cursor: text;
  transform-origin: 0 0;
  user-select: text;
}

.pv__page :deep(.pv-text > span) {
  z-index: 1;
  --font-height: 0;
  --scale-x: 1;
  --rotate: 0deg;
  font-size: calc(var(--text-scale-factor) * var(--font-height));
  transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
}

/* 选中只剩底色，而底色不在这里画：浏览器是给每一段文字各画一层，文字层里相邻两段
   的盒子是搭着的，搭着的那几个像素上就叠出更深的一块。这一条把浏览器那一层关掉，
   自己画的那一层在下面。`color` 还是要点明：正文本来是透明的，不写的话浏览器会按
   「被选中的文字」给它上色，选中的那几个字上又叠出一层同形状的字。 */
.pv__page :deep(.pv-text ::selection) {
  background: transparent;
  color: transparent;
}

/* 自己画的那一层。位置和大小由 `selectionHighlight.ts` 算，这里只管它长什么样：一块
   都别挡住选中、底色是一次填充（选区的每一块收在同一条 path 里，搭着也只落一层）。 */
.pv__page :deep(.pv-hl) {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.pv__page :deep(.pv-hl path) {
  fill: var(--selection-bg);
}
</style>
