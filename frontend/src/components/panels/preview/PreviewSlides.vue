<script setup lang="ts">
import type { SlidePageContext, SlideSource } from './slidesContext'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { useSlidesPdf } from '@/composables/useSlidesPdf'

import { contextAround } from './markdownQuote'
import SlideThumbRail from './SlideThumbRail.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'

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
 *  把身份整个再核一遍（`PanelPreviewView.canUsePageContext`），这里松一档只会选出
 *  一场白选。 */
const canQuote = computed(() => ready.value && !!props.context && !problem.value && !props.rendererMissing)
const canAskPage = computed(() => canQuote.value && !busy.value)
const showRail = computed(() => ready.value && !presenting.value && (railOverride.value ?? !narrow.value))
let observer: ResizeObserver | null = null
let restoreFocus: HTMLElement | null = null
let textGeneration = 0

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
    else root.value?.focus()
  } else {
    restoreFocus = window.document.activeElement instanceof HTMLElement ? window.document.activeElement : null
    presenting.value = true
    await nextTick()
    root.value?.focus()
  }
}
function keyboard(event: KeyboardEvent) {
  const active = window.document.activeElement
  if (!root.value?.contains(active) || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
  if (event.key === 'Escape' && presenting.value) {
    event.preventDefault()
    void presentation()
    return
  }
  if (active instanceof HTMLElement && active.closest('input, textarea, select, [contenteditable="true"]')) return
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
    emit('pageContext', {
      text,
      page: current.value,
      scope: 'selection',
      context: { ...props.context },
      ...contextAround(sheet.value, range),
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
  [current, revision, fit, size, sheet, presenting, ready],
  () => {
    const host = sheet.value
    if (!host || !ready.value) return
    void paint(current.value, host, {
      width: Math.max(32, size.value.width - 32),
      height: fit.value || presenting.value ? Math.max(32, size.value.height - 32) : undefined,
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
      narrow.value = (root.value?.clientWidth ?? 800) < 560
      if (stage.value) size.value = { width: stage.value.clientWidth, height: stage.value.clientHeight }
    })
    if (root.value) observer.observe(root.value)
    if (stage.value) observer.observe(stage.value)
  },
  { flush: 'post' }
)
onBeforeUnmount(() => {
  textGeneration += 1
  observer?.disconnect()
  if (sheet.value) cancelPaint(sheet.value)
  if (presenting.value && restoreFocus?.isConnected) restoreFocus.focus()
})
</script>

<template>
  <section
    ref="root"
    class="slides"
    :class="{ 'slides--presenting': presenting }"
    tabindex="0"
    :aria-label="t('slides.reader')"
    @keydown="keyboard"
  >
    <header class="slides__toolbar">
      <span v-if="title && !presenting" class="slides__title t-body">{{ title }}</span>
      <button v-if="ready && !presenting" type="button" :aria-pressed="showRail" @click="railOverride = !showRail">
        {{ t('slides.thumbnails') }}
      </button>
      <button type="button" :disabled="!ready || current <= 1" @click="go(current - 1)">
        {{ t('slides.previous') }}
      </button>
      <form class="slides__jump" @submit.prevent="jumpTo">
        <input
          v-model="jump"
          type="number"
          min="1"
          :max="count || 1"
          :disabled="!ready"
          :aria-label="t('slides.pageNumber')"
          @change="jumpTo"
        />
        <span class="t-meta" aria-live="polite">/ {{ count }}</span>
      </form>
      <button type="button" :disabled="!ready || current >= count" @click="go(current + 1)">
        {{ t('slides.next') }}
      </button>
      <button v-if="!presenting" type="button" :disabled="!ready" :aria-pressed="fit" @click="fit = !fit">
        {{ t(fit ? 'slides.fit' : 'slides.fitWidth') }}
      </button>
      <button type="button" :disabled="!ready" @click="presentation">
        {{ t(presenting ? 'slides.exit' : 'slides.present') }}
      </button>
      <button
        v-if="context && !presenting"
        type="button"
        :disabled="!canAskPage || pageBusy"
        :title="t('slides.wholePageHint')"
        @click="askPage"
      >
        {{ t('slides.askPage') }}
      </button>
      <button v-if="canDownload && !presenting" type="button" @click="emit('download')">
        {{ t('slides.download') }}
      </button>
    </header>
    <div v-if="!ready && busy" class="slides__state" role="status">{{ t('slides.loading') }}</div>
    <div v-else-if="!ready && (rendererMissing || problem)" class="slides__state" role="alert">
      <p>{{ t(rendererMissing ? 'slides.rendererMissing' : 'slides.openFailed') }}</p>
      <p v-if="problem" class="t-meta">{{ problem }}</p>
    </div>
    <BaseEmptyState v-else-if="!count" size="page" :title="t('slides.empty')" />
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
      <div ref="stage" class="slides__stage" @mousedown.self="root?.focus()" @mouseup="quote" @keyup.shift="quote">
        <div ref="sheet" class="slides__sheet" :data-page="current" />
      </div>
    </div>
    <p v-if="ready && busy" class="slides__notice t-meta" role="status">{{ t('slides.loading') }}</p>
    <p v-else-if="ready && (rendererMissing || problem)" class="slides__notice t-meta" role="alert">
      {{ problem || t('slides.rendererMissing') }}
    </p>
    <p v-if="pageFailure" class="slides__notice t-meta" role="alert">{{ pageFailure }}</p>
  </section>
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
  z-index: var(--z-overlay);
  /* 放映是整屏的：顶上那条工具栏（上一页 / 页码 / 下一页 / 退出）钻进刘海。让出顶部
     安全区；底边不补，幻灯片照旧铺满——Home 横杠压在画面上是放映的常态。 */
  padding-top: env(safe-area-inset-top, 0px);
}
.slides__toolbar {
  display: flex;
  flex-wrap: wrap;
  flex: none;
  align-items: center;
  gap: 4px;
  padding: 8px;
  border-bottom: 1px solid var(--line);
  background: var(--canvas);
}
.slides__title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1 1 96px;
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
.slides__jump {
  display: flex;
  align-items: center;
  gap: 4px;
}
.slides__jump input {
  width: 52px;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  color: var(--text);
  background: var(--surface);
  font-size: 13px;
}
.slides__body {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
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
.slides__sheet {
  position: relative;
  flex: none;
  background: var(--surface);
}
.slides__state {
  padding: 32px 16px;
  color: var(--muted);
  text-align: center;
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
}
.slides__sheet :deep(.slide-text-layer) {
  position: absolute;
  inset: 0;
  overflow: hidden;
  line-height: 1;
  text-size-adjust: none;
  forced-color-adjust: none;
  transform-origin: 0 0;
}
.slides__sheet :deep(.slide-text-layer span),
.slides__sheet :deep(.slide-text-layer br) {
  position: absolute;
  white-space: pre;
  color: transparent;
  cursor: text;
  transform-origin: 0 0;
}
/* PDF.js 6 TextLayer publishes geometry variables; CSS owns their application. */
.slides__sheet :deep(.slide-text-layer) {
  --min-font-size: 1;
  --text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
  --min-font-size-inv: calc(1 / var(--min-font-size));
}
.slides__sheet :deep(.slide-text-layer > :not(.markedContent)),
.slides__sheet :deep(.slide-text-layer .markedContent span:not(.markedContent)) {
  --font-height: 0;
  --scale-x: 1;
  --rotate: 0deg;
  font-size: calc(var(--text-scale-factor) * var(--font-height));
  transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
}
.slides__sheet :deep(.slide-text-layer .markedContent) {
  display: contents;
}
.slides__sheet :deep(.slide-text-layer ::selection) {
  background: var(--accent-wash);
}
.slides__sheet :deep(.slide-paint-error) {
  padding: 32px 16px;
  color: var(--danger-ink);
}
</style>
