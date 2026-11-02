<script setup lang="ts">
import type { SlideNote } from '@/lib/slideNotes'
import type { SlidePageContext, SlideSource } from './slidesContext'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { DragGesture } from '@use-gesture/vanilla'

import { usePreviewMarking } from '@/composables/panelPreview/previewMode'
import { useSlidesPdf } from '@/composables/useSlidesPdf'

import { contextAround } from './markdownQuote'
import { useSelectionHighlight } from './selectionHighlight'
import SlideNotesDock from './SlideNotesDock.vue'
import { pageRatio } from './slidesContext'
import SlideThumbRail from './SlideThumbRail.vue'
import { useSlidePresenterLink } from './useSlidePresenterLink'
import { useSlideWheel } from './useSlideWheel'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'
import { storageGet, storageSet } from '@/lib/browserStorage'
import { alignSlideNotes, hasNoteText, NOTES_MIN_PX } from '@/lib/slideNotes'
import { stepShown } from '@/lib/slidePresenter'

/** 幻灯片收成单栏的那条线，比文档那一档（`NARROW_PX` = 560）低。
 *
 *  演示稿一页就是一张 16:9 的图，缩到三百多像素宽仍然读得出；真正值得为它让出整栏的
 *  只有手机。按文档那条线（560）收，桌面默认那一格 546 宽会永远看不到缩略图轨——
 *  而轨道正是这一档最主要的翻页方式之一。 */
const COMPACT_PX = 460

/** 「标一处」（整页那一颗）只在批注模式里摆。 */
const marking = usePreviewMarking()
const props = withDefaults(
  defineProps<{
    data: ArrayBuffer | null
    title?: string
    pending?: boolean
    error?: string
    rendererMissing?: boolean
    canDownload?: boolean
    context?: SlideSource
    /** 每一页的演讲者备注和隐藏标记，按原稿的页序（`notes[0]` 是第一页）；和 PDF 怎么对页
     *  见 `alignSlideNotes`。 */
    notes?: readonly SlideNote[]
  }>(),
  {
    title: '',
    pending: false,
    error: '',
    rendererMissing: false,
    canDownload: false,
    context: undefined,
    notes: () => [],
  }
)
const emit = defineEmits<{
  quote: [payload: { text: string; page: number }]
  pageContext: [payload: SlidePageContext]
  download: []
}>()
const root = ref<HTMLElement | null>(null)
const main = ref<HTMLElement | null>(null)
const stage = ref<HTMLElement | null>(null)
const sheet = ref<HTMLElement | null>(null)
const { count, loading, failure, revision, paint, cancelPaint, pageText } = useSlidesPdf(() => props.data)
const current = ref(1)
const jump = ref('1')
const fit = ref(true)
const narrow = ref(false)
const railOverride = ref<boolean | null>(null)
const presenting = ref(false)
const pageBusy = ref(false)
const pageFailure = ref('')
const size = ref({ width: 800, height: 600 })
const busy = computed(() => props.pending || loading.value)
const problem = computed(() => props.error || failure.value)
// Refresh state describes the next conversion; already displayed bytes remain readable.
const ready = computed(() => !loading.value && !failure.value && count.value > 0)
/** 这一页现在还能不能带身份地指出去。
 *
 *  和 canAskPage 分开：整页提问要等取文字那一步空闲（`pageText` 自己要跑一趟，
 *  `busy` 里还压着父级正在取的字节），而选中一段只拿屏上已有的选区，不必等那趟。
 *
 *  但两条都建在 `ready` 上——这一页得先画出来，才有选区可谈、才知道选的是哪一版。
 *  所以转换在后台重跑（`loading` 还没落地）的那一下，两条都是假，选中一段退回那句
 *  不带版本的拼话。这不是「反正屏上那张就是读者看着的那张」：真发出去之前父级还会
 *  把身份整个再核一遍（`usePreviewLocator` 走 `usePreviewPagePin` 的 `canUse`），这里
 *  松一档只会选出一场白选。 */
const canQuote = computed(() => ready.value && !!props.context && !problem.value && !props.rendererMissing)
const canAskPage = computed(() => canQuote.value && !busy.value)
const showRail = computed(() => ready.value && !presenting.value && !narrow.value && (railOverride.value ?? true))
/** 一页四周留的空。放映时上下各让出一点，底下那行提示不压在幻灯片上。 */
const frame = computed(() => {
  if (presenting.value) return { x: 24, top: 24, bottom: 44 }
  if (narrow.value) return { x: 8, top: 8, bottom: 8 }
  return { x: 16, top: 16, bottom: 16 }
})
// ---- 演讲者备注 ----
//
// 备注是给讲的人看的，钉在阅读区底部（`SlideNotesDock`）：幻灯片按剩下的高度缩放，宽而矮的
// 面板里讲稿也一直在屏上。高度和开关都记在这台浏览器上，换一份稿子沿用。
//
// 放映时整块收掉：放映是把画面投给别人看，备注跟着上屏台下就都读到了讲稿。讲的人要边放边看
// 讲稿，用「讲者视图」——另开一个窗口（`SlidePresenter`）。
//
// 整份稿子一条备注都没有时，按钮照样在，默认不展开；点开说一句「这份演示稿没有写演讲者备注」。
// 以前这种稿子连按钮都不出现，读者分不清是稿子没写，还是没有这个功能。
const NOTES_OPEN_KEY = 'cheese.slides.notesOpen'
const NOTES_HEIGHT_KEY = 'cheese.slides.notesHeight'
const aligned = computed(() => alignSlideNotes(props.notes, count.value))
const isHidden = (page: number) => !!aligned.value[page - 1]?.hidden
const hasNotes = computed(() => aligned.value.some(hasNoteText))
const pageNote = computed(() => aligned.value[current.value - 1])
const railMarks = computed(() => aligned.value.map((note) => ({ hidden: note.hidden, noted: hasNoteText(note) })))
const notesOpen = ref(storageGet(NOTES_OPEN_KEY) !== '0')
/** 没有备注的稿子上读者自己点开过：这一份文件里一直摆着那句「没有写」。 */
const notesAsked = ref(false)
const notesHeight = ref(Math.max(NOTES_MIN_PX, Number(storageGet(NOTES_HEIGHT_KEY)) || 168))
const mainHeight = ref(600)
const notesMax = computed(() => Math.max(NOTES_MIN_PX, Math.round(mainHeight.value * 0.6)))
const notesShownHeight = computed(() => Math.min(notesHeight.value, notesMax.value))
const showNotes = computed(
  () => ready.value && !presenting.value && notesOpen.value && (hasNotes.value || notesAsked.value)
)
function toggleNotes() {
  if (showNotes.value) {
    if (hasNotes.value) setNotesOpen(false)
    notesAsked.value = false
  } else {
    setNotesOpen(true)
    notesAsked.value = true
  }
}
function setNotesOpen(open: boolean) {
  notesOpen.value = open
  storageSet(NOTES_OPEN_KEY, open ? '1' : '0')
}
function closeNotes() {
  setNotesOpen(false)
  notesAsked.value = false
}
function resizeNotes(height: number) {
  notesHeight.value = height
  storageSet(NOTES_HEIGHT_KEY, String(height))
}

// ---- 缩放 ----
//
// Ctrl / ⌘ + 滚轮、触控板双指捏合、工具栏上的 − / +、键盘 + - 0。倍数是相对「适合页面」或
// 「适合宽度」那一档的：100% 就是那一档本来的大小。放大以后舞台自己滚，指针下的那一点留在
// 指针下。放映时不缩放（投出去的就是整页）。
const ZOOM_MIN = 0.5
const ZOOM_MAX = 5
const zoom = ref(1)
const zoomLabel = computed(() => `${Math.round(zoom.value * 100)}%`)
let anchor: { x: number; y: number; fx: number; fy: number } | null = null
function zoomBy(factor: number, at?: { x: number; y: number }) {
  if (presenting.value || !ready.value) return
  const next = Math.round(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, zoom.value * factor)) * 1000) / 1000
  if (next === zoom.value) return
  const el = stage.value
  if (el) {
    const point = at ?? { x: el.clientWidth / 2, y: el.clientHeight / 2 }
    anchor = {
      ...point,
      fx: (el.scrollLeft + point.x) / Math.max(1, el.scrollWidth),
      fy: (el.scrollTop + point.y) / Math.max(1, el.scrollHeight),
    }
  }
  zoom.value = next
}
function resetZoom() {
  anchor = null
  zoom.value = 1
}

/** 往前 / 往后一页。放映时跳过隐藏页（和 PowerPoint 一样），读稿时隐藏页照常看得到。 */
function step(direction: 1 | -1) {
  if (!ready.value) return
  go(presenting.value ? stepShown(current.value, direction, count.value, isHidden) : current.value + direction)
}

/** 读屏播报用：换页时说出来的是「第 4 页，共 6 页」，不是工具栏上那个数字框。 */
const announcement = computed(() =>
  ready.value ? t('slides.pageOf', { page: current.value, count: count.value }) : ''
)

let observer: ResizeObserver | null = null
let restoreFocus: HTMLElement | null = null
let textGeneration = 0
const exitButton = ref<HTMLElement | null>(null)
/** 放映时工具栏整个收掉（和原型一样），「放映」那颗按钮也跟着卸掉——退出放映后要还焦点
 *  给它，所以单独留一个引用。 */
const presentButton = ref<HTMLElement | null>(null)

function go(page: number) {
  if (!Number.isFinite(page)) return
  current.value = Math.min(count.value || 1, Math.max(1, Math.trunc(page)))
  jump.value = String(current.value)
}
function jumpTo() {
  go(Number(jump.value))
  jump.value = String(current.value)
}
async function presentation() {
  if (presenting.value) {
    presenting.value = false
    await nextTick()
    if (restoreFocus?.isConnected) restoreFocus.focus()
    else if (presentButton.value) presentButton.value.focus()
    else root.value?.focus()
  } else {
    restoreFocus = window.document.activeElement instanceof HTMLElement ? window.document.activeElement : null
    presenting.value = true
    await nextTick()
    root.value?.focus()
  }
}
/** 放映这一层里能落焦点的地方：这一层本身、底部那条上的按钮和页码框、右上角退出键。
 *  Tab 只在这里面绕，不钻回后面的页面。 */
function presentStops(): HTMLElement[] {
  const stops: HTMLElement[] = []
  if (root.value) stops.push(root.value)
  const bar = root.value?.querySelector('.slides__bar--present')
  if (bar) {
    for (const el of bar.querySelectorAll<HTMLButtonElement | HTMLInputElement>('button, input')) {
      if (!el.disabled) stops.push(el)
    }
  }
  if (exitButton.value) stops.push(exitButton.value)
  return stops
}
/** 放映时点一下画面翻到下一页；到最后一页就停住（和原型一样，不绕回第一页）。 */
function pointAdvance() {
  if (!presenting.value || !ready.value) return
  step(1)
}
function keyboard(event: KeyboardEvent) {
  const active = window.document.activeElement
  if (!root.value?.contains(active) || event.altKey || event.ctrlKey || event.metaKey) return
  // 放映是整屏的一层：焦点留在这一层里，Tab 不能再钻回后面的页面。
  // 能落焦点的只有这一层自己和右上角那颗退出键。Shift+Tab 也算 Tab（反向绕一圈）。
  if (presenting.value && event.key === 'Tab') {
    const stops = presentStops()
    if (!stops.length) return
    event.preventDefault()
    const at = stops.indexOf(active as HTMLElement)
    const next = event.shiftKey ? (at <= 0 ? stops.length - 1 : at - 1) : (at + 1) % stops.length
    stops[next]?.focus()
    return
  }
  // Shift 只和 Tab 一起用（反向绕焦点圈）；Shift+方向键一概不管。
  if (event.shiftKey) return
  if (event.key === 'Escape' && presenting.value) {
    event.preventDefault()
    void presentation()
    return
  }
  if (active instanceof HTMLElement && active.closest('input, textarea, select, [contenteditable="true"]')) return
  // 焦点在缩略图轨里时，方向键归那条轨（在缩略图之间走），不在这里翻页。
  if (active instanceof HTMLElement && active.closest('.slide-rail')) return
  if (!ready.value) return
  const zoomKey = { '+': 1.25, '=': 1.25, '-': 0.8, '0': 0 }[event.key]
  if (zoomKey !== undefined && !presenting.value) {
    event.preventDefault()
    if (zoomKey) zoomBy(zoomKey)
    else resetZoom()
    return
  }
  const direction = (
    { ArrowRight: 1, ArrowDown: 1, PageDown: 1, ' ': 1, ArrowLeft: -1, ArrowUp: -1, PageUp: -1 } as const
  )[event.key as 'ArrowRight']
  // 空格只在放映时翻页（翻页笔、PowerPoint 的习惯）；读稿时空格留给浏览器滚动。
  if (direction !== undefined && (event.key !== ' ' || presenting.value)) {
    event.preventDefault()
    step(direction)
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    go(event.key === 'Home' ? 1 : count.value)
  }
}
// 和 PreviewPages 同一件事：浏览器给每一段文字各画一层底色，文字层里相邻两段是搭着
// 的，叠出来的那块比别处深。这一份的底色本来是实色，叠着看不出来，但换成自己画之后
// 两份才是同一套写法，见 `selectionHighlight.ts`。
useSelectionHighlight({
  sheets: () => (sheet.value ? [sheet.value] : []),
  layerClass: 'slide-text-layer',
})

function quote() {
  const selection = window.getSelection()
  if (!selection?.rangeCount || !sheet.value) return
  const range = selection.getRangeAt(0)
  if (!sheet.value.contains(range.startContainer) || !sheet.value.contains(range.endContainer)) return
  // 和父级 `onQuote` 同一条门槛：先归一化空白，再要求不止一个字。一个字的选中说不出
  // 「哪儿不对」，带上身份也只是把噪声包装得更正式。两条出口都过这一关。
  const text = selection.toString().replace(/\s+/g, ' ').trim()
  if (text.length < 2) return
  // 选中一句和整页提问走同一条出口：同一个冻结引用，带文件身份和版本，只是
  // scope 说这是这一页里的一段。没有已验证的身份时才退回原来那句拼好的话 ——
  // 它不带版本，但也不撒谎。
  if (canQuote.value && props.context) {
    // 两侧的文字帮受话人分辨同一句话在这一页的哪一处出现（整页提问没有这个说法）。
    // 量的是这一页的文字层：`sheet` 里那块画布和文字层是同一个坐标系下的两层。
    //
    // 左上角那一点也量下去：攒着一起发的那几处要靠它在页上画序号。量一页画布的
    // 内框，量不到就不带（和 `PreviewPages.onSelect` 同一条）。
    const box = sheet.value.getBoundingClientRect()
    const rect = range.getBoundingClientRect()
    const spot =
      box.width > 0 && box.height > 0
        ? { x: pageRatio((rect.left - box.left) / box.width), y: pageRatio((rect.top - box.top) / box.height) }
        : {}
    emit('pageContext', {
      text,
      page: current.value,
      scope: 'selection',
      context: { ...props.context },
      ...contextAround(sheet.value, range),
      ...spot,
    })
    return
  }
  emit('quote', { text, page: current.value })
}
async function askPage() {
  if (!props.context || !canAskPage.value || pageBusy.value) return
  const mine = ++textGeneration
  const page = current.value
  const context = { ...props.context }
  pageBusy.value = true
  pageFailure.value = ''
  try {
    const text = await pageText(page)
    if (mine !== textGeneration || !canAskPage.value || text === null) return
    if (!text) {
      pageFailure.value = t('slides.noPageText')
      return
    }
    emit('pageContext', { text, page, scope: 'page', context })
  } catch (error) {
    if (mine === textGeneration) pageFailure.value = error instanceof Error ? error.message : t('slides.textFailed')
  } finally {
    if (mine === textGeneration) pageBusy.value = false
  }
}
watch(
  [current, revision, fit, size, sheet, presenting, ready, narrow, zoom],
  async () => {
    const host = sheet.value
    if (!host || !ready.value) return
    const pad = frame.value
    const scale = presenting.value ? 1 : zoom.value
    await paint(current.value, host, {
      width: Math.max(32, (size.value.width - 2 * pad.x) * scale),
      height:
        fit.value || presenting.value ? Math.max(32, (size.value.height - pad.top - pad.bottom) * scale) : undefined,
      text: true,
      maxScale: 8,
    })
    // 缩放后把指针下那一点放回指针下（页的尺寸在 paint 里定下来，所以等它画完）。
    const el = stage.value
    if (anchor && el) {
      el.scrollLeft = anchor.fx * el.scrollWidth - anchor.x
      el.scrollTop = anchor.fy * el.scrollHeight - anchor.y
    }
    anchor = null
  },
  { flush: 'post' }
)
watch(fit, resetZoom)
watch(
  () => props.data,
  () => {
    current.value = 1
    jump.value = '1'
    zoom.value = 1
    notesAsked.value = false
  },
  { flush: 'sync' }
)
watch(
  [
    () => props.data,
    canAskPage,
    () => props.context?.topicId,
    () => props.context?.path,
    () => props.context?.source,
    () => props.context?.taskId,
    () => props.context?.version,
  ],
  () => {
    textGeneration += 1
    pageBusy.value = false
    pageFailure.value = ''
  },
  { flush: 'sync' }
)
watch(current, () => {
  jump.value = String(current.value)
  textGeneration += 1
  pageBusy.value = false
  pageFailure.value = ''
})
watch(
  [root, stage, main],
  () => {
    observer?.disconnect()
    observer = new ResizeObserver(() => {
      narrow.value = (root.value?.clientWidth ?? 800) < COMPACT_PX
      if (stage.value) size.value = { width: stage.value.clientWidth, height: stage.value.clientHeight }
      if (main.value) mainHeight.value = main.value.clientHeight
    })
    if (root.value) observer.observe(root.value)
    if (stage.value) observer.observe(stage.value)
    if (main.value) observer.observe(main.value)
  },
  { flush: 'post' }
)
// ---- 左右扫翻页（触屏） ----
//
// 手指横着快扫一下翻页，幻灯片不跟着手指挪：PowerPoint 里页面是钉住的，拖动是选字，整页跟着
// 走只会让人以为把它拖坏了。只认手指（鼠标在页上拖是在选字）；竖着的滑动留给浏览器
// （`touch-action: pan-y`）。放大了的时候不认——那时横着划是在看这一页的另一边。
let swipe: DragGesture | null = null
watch(
  stage,
  (el) => {
    swipe?.destroy()
    swipe = null
    if (!el) return
    swipe = new DragGesture(
      el,
      ({ swipe: [sx], last }) => {
        if (!ready.value || zoom.value > 1 || !last || !sx) return
        step(sx > 0 ? -1 : 1)
      },
      { axis: 'x', filterTaps: true, pointer: { touch: true } }
    )
  },
  { flush: 'post' }
)

// ---- 滚轮 ----
useSlideWheel({
  area: main,
  scroller: () => stage.value,
  enabled: () => ready.value,
  turn: step,
  zoom: (factor, at) => zoomBy(factor, at),
})

// ---- 讲者视图 ----
// 开一个窗口给讲的人自己看（讲稿、下一页、计时），这一边同时进入放映，投给台下。
const presenterBlocked = ref(false)
const presenter = useSlidePresenterLink({
  deck: () => (props.data && ready.value ? { bytes: props.data, notes: aligned.value, title: props.title } : null),
  page: current,
  go,
})
async function presenterView() {
  presenterBlocked.value = !presenter.open()
  if (!presenterBlocked.value && !presenting.value) await presentation()
}

onBeforeUnmount(() => {
  textGeneration += 1
  swipe?.destroy()
  observer?.disconnect()
  if (sheet.value) cancelPaint(sheet.value)
  if (presenting.value && restoreFocus?.isConnected) restoreFocus.focus()
})
</script>

<template>
  <!-- 放映时整块挪到 body 下：面板外面有两层带 view-transition-name 的容器（话题页、面板页签区），
       它们各自成一个绘制层级，留在里面的话 z-index 再高也压不过侧栏和分隔线，左边被侧栏盖住、
       中间透出分隔线。不放映时原地渲染。 -->
  <Teleport to="body" :disabled="!presenting">
    <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- the reader takes focus (tabindex 0) and turns pages with the arrow keys -->
    <section
      ref="root"
      class="slides"
      :class="{ 'slides--presenting': presenting, 'slides--narrow': narrow }"
      :role="presenting ? 'dialog' : 'region'"
      :aria-modal="presenting ? 'true' : undefined"
      :aria-label="t(presenting ? 'slides.presenting' : 'slides.reader')"
      tabindex="0"
      @keydown="keyboard"
    >
      <!-- 工具栏只放次级动作；翻页、页码和「放映」在主区上方那一条里，和原型一致。
           放映时整条收掉，画面铺满。 -->
      <header v-if="!presenting" class="slides__toolbar">
        <span v-if="title" class="slides__title t-body">{{ title }}</span>
        <span class="slides__spacer" />
        <div v-if="ready && !narrow" class="slides__zoom" role="group" :aria-label="t('slides.zoomReset')">
          <button
            type="button"
            class="slides__tool"
            :aria-label="t('slides.zoomOut')"
            :title="t('slides.zoomOut')"
            @click="zoomBy(0.8)"
          >
            −
          </button>
          <button
            type="button"
            class="slides__tool slides__zoom-level"
            :title="t('slides.zoomReset')"
            data-testid="slides-zoom"
            @click="resetZoom"
          >
            {{ zoomLabel }}
          </button>
          <button
            type="button"
            class="slides__tool"
            :aria-label="t('slides.zoomIn')"
            :title="t('slides.zoomIn')"
            @click="zoomBy(1.25)"
          >
            +
          </button>
        </div>
        <button
          v-if="ready"
          type="button"
          class="slides__tool"
          :aria-pressed="showNotes"
          data-testid="slides-notes-toggle"
          @click="toggleNotes"
        >
          {{ t('slides.notes') }}
        </button>
        <button
          v-if="ready && !narrow"
          type="button"
          class="slides__tool"
          :aria-pressed="showRail"
          @click="railOverride = !showRail"
        >
          {{ t('slides.thumbnails') }}
        </button>
        <button
          v-if="!narrow"
          type="button"
          class="slides__tool"
          :disabled="!ready"
          :aria-pressed="fit"
          @click="fit = !fit"
        >
          {{ t(fit ? 'slides.fit' : 'slides.fitWidth') }}
        </button>
        <button
          v-if="context && marking"
          type="button"
          class="slides__tool"
          :disabled="!canAskPage || pageBusy"
          :title="t('slides.wholePageHint')"
          @click="askPage"
        >
          {{ t('slides.askPage') }}
        </button>
        <button v-if="canDownload" type="button" class="slides__tool" @click="emit('download')">
          {{ t('slides.download') }}
        </button>
      </header>
      <div v-if="!ready && busy" class="slides__state slides__state--loading" role="status">
        <span class="slides__spinner" aria-hidden="true" />
        <p>{{ t('slides.loading') }}</p>
      </div>
      <div v-else-if="!ready && (rendererMissing || problem)" class="slides__state slides__state--failed" role="alert">
        <svg class="slides__state-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 2 1 21h22L12 2zm1 15h-2v2h2v-2zm0-8h-2v6h2V9z" />
        </svg>
        <p class="slides__state-title">{{ t(rendererMissing ? 'slides.rendererMissing' : 'slides.openFailed') }}</p>
        <p v-if="problem" class="t-meta">{{ problem }}</p>
      </div>
      <BaseEmptyState v-else-if="!count" :title="t('slides.empty')" />
      <div v-else class="slides__body">
        <SlideThumbRail
          v-if="showRail"
          :marks="railMarks"
          :count="count"
          :current="current"
          :revision="revision"
          :paint="paint"
          :cancel-paint="cancelPaint"
          @select="go"
        />
        <div ref="main" class="slides__main">
          <!-- 一页上方那一条：‹ 4 / 6 › … 放映。页码本身是个能跳的输入框，看起来还是「4 / 6」。
               放映时同一条挪到屏幕底部：翻页和跳页都还在手边，只是一片半透明的小胶囊。 -->
          <div class="slides__bar" :class="{ 'slides__bar--present': presenting }">
            <button
              type="button"
              class="slides__chev"
              :aria-label="t('slides.previous')"
              :title="t('slides.previous')"
              :disabled="!ready || current <= 1"
              @click="go(current - 1)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.4 7.4 14 6l-6 6 6 6 1.4-1.4L10.8 12z" /></svg>
            </button>
            <form class="slides__jump" @submit.prevent="jumpTo">
              <input
                v-model="jump"
                type="number"
                min="1"
                :max="count || 1"
                :disabled="!ready"
                :aria-label="t('slides.pageNumber')"
                :title="t('slides.jumpTo')"
                @change="jumpTo"
              />
              <span class="t-meta">/ {{ count }}</span>
            </form>
            <span
              v-if="isHidden(current)"
              class="slides__hidden"
              :title="t('slides.hiddenSlideHint')"
              data-testid="slides-hidden"
              >{{ t('slides.hiddenSlide') }}</span
            >
            <button
              type="button"
              class="slides__chev"
              :aria-label="t('slides.next')"
              :title="t('slides.next')"
              :disabled="!ready || current >= count"
              @click="go(current + 1)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8.6 16.6 10 18l6-6-6-6-1.4 1.4L13.2 12z" /></svg>
            </button>
            <span v-if="!presenting" class="slides__spacer" />
            <span v-if="presenting" class="slides__bar-hint">{{ t('slides.presentHint') }}</span>
            <button
              v-if="!presenting && !narrow"
              type="button"
              class="slides__present slides__present--quiet"
              :disabled="!ready"
              :title="t('slides.presenterHint')"
              data-testid="slides-presenter"
              @click="presenterView"
            >
              {{ t('slides.presenterView') }}
            </button>
            <button
              v-if="!presenting"
              ref="presentButton"
              type="button"
              class="slides__present"
              :disabled="!ready"
              @click="presentation"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M3 4h18v12H3zM3 18h18v2H3z" opacity=".45" />
                <path d="M10 8v6l5-3z" />
              </svg>
              {{ t('slides.present') }}
            </button>
          </div>
          <!-- The stage never holds keyboard focus (the reader around it does), so only a pointer selection is quoted here. -->
          <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions, vuejs-accessibility/click-events-have-key-events -- a mouse selection on the slide is quoted and (while presenting) a click turns the page; the keyboard turns pages on the reader around it, which is why the stage itself is not focusable -->
          <div ref="stage" class="slides__stage" @mousedown.self="root?.focus()" @mouseup="quote" @click="pointAdvance">
            <div
              ref="sheet"
              class="slides__sheet"
              :class="{ 'is-hidden-slide': isHidden(current) && !presenting }"
              :data-page="current"
            />
          </div>
          <SlideNotesDock
            v-if="showNotes"
            :note="pageNote"
            :any-notes="hasNotes"
            :page="current"
            :height="notesShownHeight"
            :max-height="notesMax"
            @resize="resizeNotes"
            @close="closeNotes"
          />
        </div>
      </div>
      <!-- 放映没有工具栏；留一颗看得见的出口，触屏上没有 Esc 也退得出来（原型只有那句提示）。-->
      <button
        v-if="presenting"
        ref="exitButton"
        type="button"
        class="slides__exit"
        :aria-label="t('slides.exit')"
        :title="t('slides.exit')"
        @click="presentation"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="m6.4 5 5.6 5.6L17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6L6.4 19 5 17.6l5.6-5.6L5 6.4z" />
        </svg>
      </button>
      <p class="visually-hidden" aria-live="polite">{{ announcement }}</p>
      <p v-if="ready && busy" class="slides__notice t-meta" role="status">{{ t('slides.loading') }}</p>
      <p v-else-if="ready && (rendererMissing || problem)" class="slides__notice t-meta" role="alert">
        {{ problem || t('slides.rendererMissing') }}
      </p>
      <p v-if="pageFailure" class="slides__notice t-meta" role="alert">{{ pageFailure }}</p>
      <p v-if="presenterBlocked" class="slides__notice t-meta" role="alert">{{ t('slides.presenterBlocked') }}</p>
    </section>
  </Teleport>
</template>

<style scoped>
.slides {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  color: var(--text);
  background: var(--surface);
  container-type: inline-size;
}
.slides:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.slides--presenting {
  position: fixed;
  inset: 0;
  /* 放映是一整层黑场：幻灯片之外什么都不留，和原型一样。 */
  /* 整屏黑场：不透明的暗底，两套主题下一致。放映要独占整屏——图片浮层那层是 92% 透明
     的（它要让底下的页面若隐若现），放一张幻灯片时那个「隐约」就是别人家的界面。 */
  background: var(--present-ground);
  color: var(--present-ink);
  /* 挪到 body 下以后它和弹窗同在一层比高低：放在 Vuetify 弹窗下面一档，放映中按 ? 打开的
     快捷键帮助、确认框这些照样盖在放映上面，而侧栏、分隔线都在它下面。 */
  z-index: var(--z-overlay-below-vuetify);
  /* 放映是整屏的：让出顶部安全区，底边不补（Home 横杠压在画面上是放映的常态）。 */
  padding-top: env(safe-area-inset-top, 0px);
}
/* 聚焦的是整层黑场，焦点圈画在屏幕边上没有意义；退出靠 Esc 和底下那行提示。 */
.slides--presenting:focus-visible {
  outline: none;
}
.slides__toolbar {
  display: flex;
  flex-wrap: wrap;
  flex: none;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--line);
  background: var(--canvas);
}
.slides__toolbar button {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: var(--lh-13);
}
.slides__toolbar button:hover:not(:disabled) {
  background: var(--fill-2);
}
.slides__toolbar button:disabled {
  color: var(--faint);
}
.slides__toolbar button:focus-visible,
.slides__jump input:focus-visible {
  outline: 2px solid var(--accent);
}
.slides__title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1 1 96px;
}
.slides__spacer {
  flex: 1;
}
.slides__body {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
}
.slides__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
/* 一页上方那一条：‹ 4 / 6 › … 放映。原型就在这个位置，字号 13、间距 4。 */
.slides__bar {
  display: flex;
  flex: none;
  align-items: center;
  gap: 2px;
  padding: 6px 12px 0;
  font-size: 13px;
}
.slides__chev {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--radius-sm);
  color: var(--muted);
}
.slides__chev svg {
  width: 18px;
  height: 18px;
  fill: currentColor;
}
.slides__chev:hover:not(:disabled) {
  background: var(--fill-2);
  color: var(--text);
}
.slides__chev:disabled {
  color: var(--faint);
}
.slides__chev:focus-visible {
  outline: 2px solid var(--accent);
}
.slides__jump {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--muted);
}
/* 看起来是「4 / 6」里的那个 4，点进去就能改。去掉数字框的上下箭头，免得它比字还显眼。 */
.slides__jump input {
  width: 2.2em;
  padding: 2px 2px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text);
  background: transparent;
  font: inherit;
  text-align: right;
  appearance: textfield;
  -moz-appearance: textfield;
}
.slides__jump input::-webkit-outer-spin-button,
.slides__jump input::-webkit-inner-spin-button {
  margin: 0;
  appearance: none;
}
.slides__jump input:hover:not(:disabled) {
  border-color: var(--line-2);
}
.slides__jump input:focus {
  border-color: var(--accent);
  background: var(--surface);
}
.slides__present {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 13px;
}
.slides__present svg {
  width: 16px;
  height: 16px;
  fill: currentColor;
}
.slides__present:hover:not(:disabled) {
  background: var(--fill-2);
}
.slides__present:disabled {
  color: var(--faint);
}
.slides__present:focus-visible {
  outline: 2px solid var(--accent);
}
.slides__stage {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  align-items: safe center;
  justify-content: safe center;
  padding: 16px;
  background: var(--canvas);
}
.slides--presenting .slides__stage {
  padding: 24px 24px 44px;
  background: transparent;
  cursor: pointer;
}
.slides__sheet {
  position: relative;
  flex: none;
  background: var(--surface);
  border-radius: 8px;
  box-shadow: var(--shadow-2);
}
/* 隐藏页照常看得到（和 PowerPoint 的编辑视图一样），压暗一点，页码旁标着「隐藏」。 */
.slides__sheet.is-hidden-slide {
  opacity: 0.6;
}
.slides__hidden {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
  color: var(--muted);
  background: var(--fill-2);
}
.slides__zoom {
  display: inline-flex;
  align-items: center;
}
.slides__zoom-level {
  min-width: 52px;
  font-variant-numeric: tabular-nums;
}
.slides__present--quiet {
  margin-right: 6px;
  border-color: transparent;
  color: var(--muted);
}
/* 窄处：一页铺满宽度，四周只留 8px；横着滑归翻页，竖着滑照旧滚。缩略图和「适合页面」
   不摆——一栏缩略图在手机上要占掉三分之一屏，而一页本来就是按宽度铺的。 */
.slides--narrow .slides__stage {
  padding: 8px;
  touch-action: pan-y;
}
.slides--narrow .slides__bar {
  padding: 4px 8px 0;
}
.slides__exit {
  position: fixed;
  top: 16px;
  right: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--present-fill);
  color: var(--present-ink);
}
.slides__exit svg {
  width: 18px;
  height: 18px;
  fill: currentColor;
}
.slides__exit:hover {
  background: var(--present-fill-2);
}
.slides__exit:focus-visible {
  outline: 2px solid var(--present-ink);
  outline-offset: 2px;
}
</style>

<style scoped src="./slides-stage.css"></style>
<style src="./preview-selection-paint.css"></style>
