<script setup lang="ts">
import type { FileSource } from '@/cx_types'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { useSlidesPdf } from '@/composables/useSlidesPdf'

import SlideThumbRail from './SlideThumbRail.vue'

import { t } from '@/i18n'

export type SlideSource = { topicId: string; path: string; source: FileSource; taskId?: string | null; version: string }
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
  pageContext: [payload: { text: string; page: number; scope: 'page'; context: SlideSource }]
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
const ready = computed(() => !busy.value && !problem.value && !props.rendererMissing && count.value > 0)
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
  if (active instanceof HTMLElement && active.closest('input, textarea, select, [contenteditable="true"]')) return
  if (event.key === 'Escape' && presenting.value) {
    event.preventDefault()
    void presentation()
    return
  }
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
  const text = selection.toString().trim()
  if (text) emit('quote', { text, page: current.value })
}
async function askPage() {
  if (!props.context || !ready.value || pageBusy.value) return
  const mine = ++textGeneration
  const page = current.value
  const context = { ...props.context }
  pageBusy.value = true
  pageFailure.value = ''
  try {
    const text = await pageText(page)
    if (mine !== textGeneration || text === null) return
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
  [current, revision, fit, size, sheet],
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
  [() => props.data, () => props.context],
  () => {
    current.value = 1
    jump.value = '1'
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
        :disabled="!ready || pageBusy"
        :title="t('slides.wholePageHint')"
        @click="askPage"
      >
        {{ t('slides.askPage') }}
      </button>
      <button v-if="canDownload && !presenting" type="button" @click="emit('download')">
        {{ t('slides.download') }}
      </button>
    </header>
    <div v-if="busy" class="slides__state" role="status">{{ t('slides.loading') }}</div>
    <div v-else-if="rendererMissing || problem" class="slides__state" role="alert">
      <p>{{ t(rendererMissing ? 'slides.rendererMissing' : 'slides.openFailed') }}</p>
      <p v-if="problem" class="t-meta">{{ problem }}</p>
    </div>
    <div v-else-if="!count" class="slides__state">{{ t('slides.empty') }}</div>
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
  z-index: 2400;
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
.slides__sheet :deep(.slide-text-layer ::selection) {
  background: var(--accent-wash);
}
.slides__sheet :deep(.slide-paint-error) {
  padding: 32px 16px;
  color: var(--danger-ink);
}
</style>
