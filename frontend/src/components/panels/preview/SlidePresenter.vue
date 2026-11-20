<script setup lang="ts">
// 讲者视图的画面：讲的人自己看的那个窗口（放映的那一页在另一个窗口里，投给台下）。
//
// 照 PowerPoint 的讲者视图摆：左边一大张当前页，右边上方是下一页，下面是这一页的讲稿；顶上
// 一条是计时器、现在几点、第几页。翻页的键和放映一样（← → 空格 PageUp/PageDown，翻页笔发的
// 就是后两个），滚轮也翻；隐藏页照放映的规矩跳过。
//
// 只凭 props 画、只发 `go`：稿子从哪来、和放映那边怎么对话，由页面那一层管
// （`views/SlidesPresenterView.vue`）。
import type { SlideNote } from '@/lib/slideNotes'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useSlidesPdf } from '@/composables/useSlidesPdf'

import SlideNotesDock from './SlideNotesDock.vue'
import { useSlideWheel } from './useSlideWheel'

import { t } from '@/i18n'
import { storageGet, storageSet } from '@/lib/browserStorage'
import { hasNoteText } from '@/lib/slideNotes'
import { stepShown } from '@/lib/slidePresenter'

const props = withDefaults(
  defineProps<{
    data: ArrayBuffer | null
    notes?: readonly SlideNote[]
    page: number
    title?: string
    /** 放映那边已经关了：画面留着，不再跟着翻。 */
    ended?: boolean
  }>(),
  { notes: () => [], title: '', ended: false }
)
const emit = defineEmits<{ go: [page: number] }>()

const { count, loading, failure, revision, paint, cancelPaint } = useSlidesPdf(() => props.data)
const currentHost = ref<HTMLElement | null>(null)
const nextHost = ref<HTMLElement | null>(null)
const currentBox = ref<HTMLElement | null>(null)
const nextBox = ref<HTMLElement | null>(null)
const area = ref<HTMLElement | null>(null)
const sizes = ref({ current: { width: 800, height: 450 }, next: { width: 320, height: 180 } })

const hidden = (page: number) => !!props.notes[page - 1]?.hidden
const nextPage = computed(() => {
  const next = stepShown(props.page, 1, count.value, hidden)
  return next === props.page ? 0 : next
})
const note = computed(() => props.notes[props.page - 1])
const anyNotes = computed(() => props.notes.some(hasNoteText))

function go(direction: 1 | -1) {
  if (!count.value) return
  const page = stepShown(props.page, direction, count.value, hidden)
  if (page !== props.page) emit('go', page)
}

// ---- 计时 ----
// 从打开讲者视图开始走；可以暂停、清零。「现在几点」单独一格，讲的人常常两样都要看。
const started = ref(Date.now())
const pausedAt = ref<number | null>(null)
const tick = ref(Date.now())
let timer: number | undefined
const elapsed = computed(() => Math.max(0, (pausedAt.value ?? tick.value) - started.value))
const clock = computed(() => new Date(tick.value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))
function format(ms: number) {
  const s = Math.floor(ms / 1000)
  const h = Math.floor(s / 3600)
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}
function togglePause() {
  if (pausedAt.value === null) pausedAt.value = Date.now()
  else {
    started.value += Date.now() - pausedAt.value
    pausedAt.value = null
  }
}
function resetTimer() {
  started.value = Date.now()
  if (pausedAt.value !== null) pausedAt.value = started.value
}

// ---- 讲稿字号 ----
// 讲的人离屏幕远，默认比面板里大；调过的记住。
const FONT_KEY = 'cheese.slides.presenterFont'
const fontSize = ref(Math.min(40, Math.max(14, Number(storageGet(FONT_KEY)) || 20)))
function font(delta: number) {
  fontSize.value = Math.min(40, Math.max(14, fontSize.value + delta))
  storageSet(FONT_KEY, String(fontSize.value))
}

function keyboard(event: KeyboardEvent) {
  if (event.altKey || event.ctrlKey || event.metaKey) return
  const target = event.target
  if (target instanceof HTMLElement && target.closest('input, textarea, select, [contenteditable="true"]')) return
  const direction = (
    {
      ArrowRight: 1,
      ArrowDown: 1,
      PageDown: 1,
      ' ': 1,
      Enter: 1,
      n: 1,
      ArrowLeft: -1,
      ArrowUp: -1,
      PageUp: -1,
      Backspace: -1,
      p: -1,
    } as Record<string, 1 | -1>
  )[event.key]
  if (direction) {
    event.preventDefault()
    go(event.shiftKey && event.key === ' ' ? -1 : direction)
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    emit('go', event.key === 'Home' ? 1 : count.value)
  }
}

useSlideWheel({
  area,
  scroller: () => null,
  enabled: () => count.value > 0,
  turn: go,
})

let observer: ResizeObserver | null = null
function measure() {
  const c = currentBox.value
  const n = nextBox.value
  sizes.value = {
    current: { width: c?.clientWidth || 800, height: c?.clientHeight || 450 },
    next: { width: n?.clientWidth || 320, height: n?.clientHeight || 180 },
  }
}
// 两格在稿子到了之后才出现（`v-if="count"`），所以等它们出现了再量。
watch(
  [currentBox, nextBox],
  ([current, next]) => {
    observer?.disconnect()
    observer = new ResizeObserver(measure)
    if (current) observer.observe(current)
    if (next) observer.observe(next)
    measure()
  },
  { flush: 'post' }
)
onMounted(() => {
  window.addEventListener('keydown', keyboard)
  timer = window.setInterval(() => (tick.value = Date.now()), 1000)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', keyboard)
  window.clearInterval(timer)
  observer?.disconnect()
  if (currentHost.value) cancelPaint(currentHost.value)
  if (nextHost.value) cancelPaint(nextHost.value)
})

watch(
  [() => props.page, nextPage, revision, sizes, count],
  () => {
    if (!count.value) return
    if (currentHost.value) {
      void paint(Math.min(props.page, count.value), currentHost.value, {
        width: Math.max(32, sizes.value.current.width),
        height: Math.max(32, sizes.value.current.height),
        maxScale: 4,
      })
    }
    if (nextHost.value) {
      if (nextPage.value)
        void paint(nextPage.value, nextHost.value, {
          width: Math.max(32, sizes.value.next.width),
          height: Math.max(32, sizes.value.next.height),
        })
      else cancelPaint(nextHost.value)
    }
  },
  { flush: 'post', deep: true }
)
</script>

<template>
  <div ref="area" class="presenter">
    <header class="presenter__bar">
      <span class="presenter__title">{{ title || t('slides.presenterView') }}</span>
      <span v-if="ended" class="presenter__ended t-meta">{{ t('slides.presenterEnded') }}</span>
      <span class="presenter__spacer" />
      <span class="presenter__timer" :class="{ 'is-paused': pausedAt !== null }" aria-live="off">{{
        format(elapsed)
      }}</span>
      <button type="button" class="presenter__tool" @click="togglePause">
        {{ t(pausedAt === null ? 'slides.timerPause' : 'slides.timerResume') }}
      </button>
      <button type="button" class="presenter__tool" @click="resetTimer">{{ t('slides.timerReset') }}</button>
      <span class="presenter__clock">{{ clock }}</span>
    </header>
    <div v-if="!count" class="presenter__wait" role="status">
      <p>{{ failure || (loading || !data ? t('slides.presenterWaiting') : t('slides.empty')) }}</p>
    </div>
    <div v-else class="presenter__body">
      <section class="presenter__current" :aria-label="t('slides.pageOf', { page, count })">
        <div ref="currentBox" class="presenter__frame">
          <div ref="currentHost" class="presenter__sheet" />
        </div>
        <nav class="presenter__nav">
          <button type="button" class="presenter__chev" :disabled="page <= 1" @click="go(-1)">
            ‹ {{ t('slides.previous') }}
          </button>
          <span class="presenter__page"
            >{{ page }} / {{ count
            }}<span v-if="hidden(page)" class="presenter__hidden">{{ t('slides.hiddenSlide') }}</span></span
          >
          <button type="button" class="presenter__chev" :disabled="!nextPage" @click="go(1)">
            {{ t('slides.next') }} ›
          </button>
        </nav>
      </section>
      <aside class="presenter__side">
        <div class="presenter__label t-meta">
          {{ nextPage ? t('slides.nextSlide', { page: nextPage }) : t('slides.endOfShow') }}
        </div>
        <div ref="nextBox" class="presenter__next">
          <div v-show="nextPage" ref="nextHost" class="presenter__sheet presenter__sheet--next" />
        </div>
        <SlideNotesDock
          class="presenter__notes"
          :note="note"
          :any-notes="anyNotes"
          :page="page"
          :height="0"
          :max-height="0"
          :font-size="fontSize"
          :resizable="false"
        >
          <template #tools>
            <button type="button" class="presenter__tool" :aria-label="t('slides.fontSmaller')" @click="font(-2)">
              A−
            </button>
            <button type="button" class="presenter__tool" :aria-label="t('slides.fontLarger')" @click="font(2)">
              A+
            </button>
          </template>
        </SlideNotesDock>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.presenter {
  display: flex;
  flex-direction: column;
  height: 100dvh;
  color: var(--text);
  background: var(--canvas);
}
.presenter__bar {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.presenter__title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}
.presenter__spacer {
  flex: 1;
}
.presenter__timer {
  font-size: 22px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.presenter__timer.is-paused {
  color: var(--muted);
}
.presenter__clock {
  margin-left: 8px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.presenter__tool {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--text);
}
.presenter__tool:hover {
  background: var(--fill-2);
}
.presenter__tool:focus-visible,
.presenter__chev:focus-visible {
  outline: 2px solid var(--accent);
}
.presenter__wait {
  display: grid;
  flex: 1;
  place-items: center;
  color: var(--muted);
}
.presenter__body {
  display: grid;
  flex: 1;
  grid-template-columns: minmax(0, 3fr) minmax(260px, 2fr);
  gap: 16px;
  min-height: 0;
  padding: 16px;
}
.presenter__current,
.presenter__side {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  gap: 8px;
}
.presenter__frame,
.presenter__next {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
}
.presenter__frame {
  flex: 1;
}
.presenter__next {
  flex: 0 0 34%;
}
.presenter__sheet {
  position: relative;
  flex: none;
  overflow: hidden;
  border-radius: 6px;
  background: var(--surface);
  box-shadow: var(--shadow-2);
}
.presenter__nav {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  gap: 16px;
}
.presenter__chev {
  padding: 6px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface);
}
.presenter__chev:disabled {
  color: var(--faint);
}
.presenter__page {
  font-variant-numeric: tabular-nums;
}
.presenter__hidden {
  margin-left: 8px;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  color: var(--muted);
  background: var(--fill-2);
}
.presenter__label {
  flex: none;
}
.presenter__notes {
  border: 1px solid var(--line);
  border-radius: 8px;
}
@media (max-width: 760px) {
  .presenter__body {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) minmax(0, 1fr);
  }
}
</style>
