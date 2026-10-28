<script setup lang="ts">
import type { SlidePageContext, SlideSource } from './slidesContext'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { DragGesture } from '@use-gesture/vanilla'

import { useSlidesPdf } from '@/composables/useSlidesPdf'

import { contextAround } from './markdownQuote'
import { useSelectionHighlight } from './selectionHighlight'
import { pageRatio } from './slidesContext'
import SlideThumbRail from './SlideThumbRail.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'

/** 幻灯片收成单栏的那条线，比文档那一档（`NARROW_PX` = 560）低。
 *
 *  演示稿一页就是一张 16:9 的图，缩到三百多像素宽仍然读得出；真正值得为它让出整栏的
 *  只有手机。按文档那条线（560）收，桌面默认那一格 546 宽会永远看不到缩略图轨——
 *  而轨道正是这一档最主要的翻页方式之一。 */
const COMPACT_PX = 460

const props = withDefaults(
  defineProps<{
    data: ArrayBuffer | null
    title?: string
    pending?: boolean
    error?: string
    rendererMissing?: boolean
    canDownload?: boolean
    context?: SlideSource
  }>(),
  { title: '', pending: false, error: '', rendererMissing: false, canDownload: false, context: undefined }
)
const emit = defineEmits<{
  quote: [payload: { text: string; page: number }]
  pageContext: [payload: SlidePageContext]
  download: []
}>()
const root = ref<HTMLElement | null>(null)
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
/** 放映时点一下画面翻到下一页；到最后一页就停住（和原型一样，不绕回第一页）。 */
function pointAdvance() {
  if (!presenting.value || !ready.value) return
  if (current.value < count.value) go(current.value + 1)
}
function keyboard(event: KeyboardEvent) {
  const active = window.document.activeElement
  if (!root.value?.contains(active) || event.altKey || event.ctrlKey || event.metaKey) return
  // 放映是整屏的一层：焦点留在这一层里，Tab 不能再钻回后面的页面。
  // 能落焦点的只有这一层自己和右上角那颗退出键。Shift+Tab 也算 Tab（反向绕一圈）。
  if (presenting.value && event.key === 'Tab') {
    const stops = [root.value, exitButton.value].filter((el): el is HTMLElement => !!el)
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
  const page = {
    ArrowRight: current.value + 1,
    ArrowDown: current.value + 1,
    PageDown: current.value + 1,
    ArrowLeft: current.value - 1,
    ArrowUp: current.value - 1,
    PageUp: current.value - 1,
    Home: 1,
    End: count.value,
  }[event.key]
  if (page !== undefined) {
    event.preventDefault()
    go(page)
  }
}
// 和 PreviewPages 同一件事：浏览器给每一段文字各画一层底色，文字层里相邻两段是搭着
// 的，叠出来的那块比别处深。这一份的底色本来是实色，叠着看不出来，但换成自己画之后
// 两份才是同一套写法，见 `selectionHighlight.ts`。
useSelectionHighlight({
  sheets: () => (sheet.value ? [sheet.value] : []),
  layerClass: 'slide-text-layer',
  paintClass: 'slide-hl',
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
  [current, revision, fit, size, sheet, presenting, ready, narrow],
  () => {
    const host = sheet.value
    if (!host || !ready.value) return
    const pad = frame.value
    void paint(current.value, host, {
      width: Math.max(32, size.value.width - 2 * pad.x),
      height: fit.value || presenting.value ? Math.max(32, size.value.height - pad.top - pad.bottom) : undefined,
      text: true,
    })
  },
  { flush: 'post' }
)
watch(
  () => props.data,
  () => {
    current.value = 1
    jump.value = '1'
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
  [root, stage],
  () => {
    observer?.disconnect()
    observer = new ResizeObserver(() => {
      narrow.value = (root.value?.clientWidth ?? 800) < COMPACT_PX
      if (stage.value) size.value = { width: stage.value.clientWidth, height: stage.value.clientHeight }
    })
    if (root.value) observer.observe(root.value)
    if (stage.value) observer.observe(stage.value)
  },
  { flush: 'post' }
)
// ---- 左右滑翻页 ----
//
// 手指横着拖，这一页跟着走；松手时甩得够快、或者拖过了三分之一页宽，就翻到下一页 / 上一页，
// 不够就弹回原处。只认手指：鼠标在页上拖是在选字。竖着的滑动留给浏览器（`touch-action:
// pan-y`），这一格比一页高的时候照样能上下滚。
const dragX = ref(0)
let swipe: DragGesture | null = null
watch(
  stage,
  (el) => {
    swipe?.destroy()
    swipe = null
    if (!el) return
    swipe = new DragGesture(
      el,
      ({ active, movement: [mx], swipe: [sx], last }) => {
        if (!ready.value) return
        dragX.value = active ? mx : 0
        if (!last) return
        const page = sx ? -sx : Math.abs(mx) > el.clientWidth / 3 ? -Math.sign(mx) : 0
        if (page) go(current.value + page)
      },
      { axis: 'x', filterTaps: true, pointer: { touch: true } }
    )
  },
  { flush: 'post' }
)

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
          v-if="context"
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
          :count="count"
          :current="current"
          :revision="revision"
          :paint="paint"
          :cancel-paint="cancelPaint"
          @select="go"
        />
        <div class="slides__main">
          <!-- 一页上方那一条：‹ 4 / 6 › … 放映。页码本身是个能跳的输入框，看起来还是「4 / 6」。 -->
          <div v-if="!presenting" class="slides__bar">
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
            <span class="slides__spacer" />
            <button ref="presentButton" type="button" class="slides__present" :disabled="!ready" @click="presentation">
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
              :class="{ 'is-dragging': dragX !== 0 }"
              :data-page="current"
              :style="dragX ? { transform: `translateX(${dragX}px)` } : undefined"
            />
          </div>
        </div>
      </div>
      <p v-if="presenting && ready" class="slides__hint" aria-hidden="true">
        {{ t('slides.presentHint') }} · {{ current }} / {{ count }}
      </p>
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
  /* 整屏黑场用图片浮层同一族的令牌：两套主题下都是暗底、浅字。 */
  background: var(--lightbox-ground);
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
  /* 松手弹回原处时有个过程；跟着手指走的那一段不能有，否则一路慢半拍。 */
  transition: transform var(--dur-quick) var(--ease-standard);
}
.slides__sheet.is-dragging {
  transition: none;
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
  background: var(--lightbox-fill);
  color: var(--lightbox-ink);
}
.slides__exit svg {
  width: 18px;
  height: 18px;
  fill: currentColor;
}
.slides__exit:hover {
  background: var(--lightbox-fill-2);
}
.slides__exit:focus-visible {
  outline: 2px solid var(--lightbox-ink);
  outline-offset: 2px;
}
.slides--presenting .slides__hint {
  position: fixed;
  right: 0;
  bottom: 16px;
  left: 0;
  margin: 0;
  color: var(--lightbox-muted);
  font-size: 12px;
  text-align: center;
  pointer-events: none;
}
.slides__state {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 32px 24px;
  color: var(--muted);
  text-align: center;
  background: var(--canvas);
}
.slides__state p {
  margin: 0;
  max-width: 40ch;
}
.slides__state-title {
  color: var(--text);
  font-size: 15px;
}
.slides__state-icon {
  width: 28px;
  height: 28px;
  fill: var(--faint);
}
.slides__state--loading {
  gap: 12px;
}
.slides__spinner {
  width: 24px;
  height: 24px;
  border: 2px solid var(--line-2);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: slides-spin 0.8s linear infinite;
}
@keyframes slides-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .slides__spinner {
    animation-duration: 2.4s;
  }
  .slides__sheet {
    transition: none;
  }
}
.slides__notice {
  margin: 0;
  padding: 8px 16px;
  color: var(--danger-ink);
}
.slides__notice[role='status'] {
  color: var(--muted);
}
.slides__sheet :deep(canvas) {
  display: block;
  border-radius: 8px;
}
/* 这一份和 PreviewPages 里 `.pv-text` 那一段是同一套写法：改一处时两处一起改。
 *
 * pdf.js 6 只把几何写成变量，字号和 transform 得由样式从这里乘出来。少了这一段，
 * 字还是透明的、位置也对，坏在看不见的地方——每个 span 按继承来的字号排版，盒子
 * 大小和纸上那些字对不上，选中落在隔壁，高亮盖不住。 */
.slides__sheet :deep(.slide-text-layer) {
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
.slides__sheet :deep(.slide-text-layer span),
.slides__sheet :deep(.slide-text-layer br) {
  position: absolute;
  white-space: pre;
  color: transparent;
  cursor: text;
  transform-origin: 0 0;
  user-select: text;
}
.slides__sheet :deep(.slide-text-layer > :not(.markedContent)),
.slides__sheet :deep(.slide-text-layer .markedContent span:not(.markedContent)) {
  z-index: 1;
  --font-height: 0;
  --scale-x: 1;
  --rotate: 0deg;
  font-size: calc(var(--text-scale-factor) * var(--font-height));
  transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
}
.slides__sheet :deep(.slide-text-layer .markedContent) {
  display: contents;
}
/* 选中只剩底色，底色不在这里画——和 PreviewPages 里那一段同一套写法，改一处时两处
   一起改。这一条把浏览器那一层关掉，自己画的那一层在下面。`color` 还是要点明：正文
   本来是透明的，不写的话浏览器会按「被选中的文字」给它上色，选中的那几个字上又叠出
   一层同形状的字。 */
.slides__sheet :deep(.slide-text-layer ::selection) {
  background: transparent;
  color: transparent;
}
/* 自己画的那一层，由 `selectionHighlight.ts` 算位置和大小。 */
.slides__sheet :deep(.slide-hl) {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.slides__sheet :deep(.slide-hl path) {
  fill: var(--accent-wash);
}
.slides__sheet :deep(.slide-paint-error) {
  padding: 32px 16px;
  color: var(--danger-ink);
}
</style>
