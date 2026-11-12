<script setup lang="ts">
// The code surface of a code file in 项目文件 (DemoCodeEdit, where people type
// into the live project) and of a 一件事's 文件 (ThingFiles, read-only unless
// I took it over): a line-number gutter and a plain textarea in the diff
// view's type and rhythm. Over the text it draws the caret of whoever else is
// typing, with their name (the documents' caret, in their colour; 芝士's while
// it works), a band on lines brought into view from a mention, and the
// passive marker of a 一件事 changing some lines (a band, and the tag
// 「芝士在改这几行」), which is not a lock: its tag opens the 一件事.
//
// A plain textarea, not the product's Monaco editor: Monaco needs web workers
// and several MB, and the demo page runs as one file in a null-origin sandbox.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

export interface Peer {
  handle: string
  name: string
  color: string
  /** 1-based line, 0-based column. */
  line: number
  col: number
}

/** Lines a 一件事 is changing, 1-based and inclusive, and what its tag says. */
export interface LineTag {
  key: string
  start: number
  end: number
  label: string
  color: string
}

const props = defineProps<{
  label: string
  readonly?: boolean
  peers?: Peer[]
  tags?: LineTag[]
  /** Lines to bring into view and mark; `tick` changes on every new request. */
  mark?: { start: number; end: number; tick: number } | null
}>()
const emit = defineEmits<{ (e: 'tag', key: string): void }>()
const text = defineModel<string>({ required: true })
const lines = computed(() => text.value.split('\n'))
// The textarea is as wide as its longest line and the column scrolls sideways: a textarea scrolling
// on its own would leave the carets and the band behind, and its scrollbar would cover the last line.
const cols = computed(() => Math.max(...lines.value.map((l) => l.length)) + 2)

const scroller = ref<HTMLElement | null>(null)
const gutter = ref<HTMLElement | null>(null)

// A 一件事's tag sits at the right edge of what is in view, wherever the column has scrolled sideways: where
// that edge is, in the text's own coordinates.
const tagRight = ref(0)
function placeTags() {
  const sc = scroller.value
  if (!sc) return
  tagRight.value = sc.scrollLeft + sc.clientWidth - (gutter.value?.offsetWidth ?? 0) - 19 - 8
}
let ro: ResizeObserver | null = null
onMounted(() => {
  placeTags()
  scroller.value?.addEventListener('scroll', placeTags, { passive: true })
  ro = new ResizeObserver(placeTags)
  if (scroller.value) ro.observe(scroller.value)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  scroller.value?.removeEventListener('scroll', placeTags)
})
const area = ref<HTMLTextAreaElement | null>(null)

// Whoever else is typing stays in view: their caret's line is scrolled to when it leaves the visible part.
watch(
  () => props.peers?.[0]?.line,
  async (line) => {
    await nextTick()
    const sc = scroller.value
    const a = area.value
    if (!line || !sc || !a) return
    const lh = parseFloat(getComputedStyle(a).lineHeight) || 18.6
    const y = (line - 1) * lh
    if (y < sc.scrollTop || y > sc.scrollTop + sc.clientHeight - 3 * lh)
      sc.scrollTop = Math.max(0, y - sc.clientHeight / 3)
  }
)

// A mention asks for some lines: scroll them to a third of the way down, select them, and flash a band.
const band = ref<{ start: number; end: number } | null>(null)
watch(
  () => props.mark?.tick,
  async () => {
    const m = props.mark
    if (!m) return
    band.value = { start: m.start, end: Math.max(m.start, m.end) }
    await nextTick()
    const sc = scroller.value
    const a = area.value
    if (!sc || !a) return
    const lh = parseFloat(getComputedStyle(a).lineHeight) || 18.6
    sc.scrollTop = Math.max(0, (m.start - 1) * lh - sc.clientHeight / 3)
    const starts = [0]
    for (let i = 0; i < text.value.length; i++) if (text.value[i] === '\n') starts.push(i + 1)
    const from = starts[m.start - 1] ?? 0
    const to = (starts[band.value.end] ?? text.value.length + 1) - 1
    a.focus({ preventScroll: true })
    a.setSelectionRange(from, Math.max(from, to))
    setTimeout(() => (band.value = null), 2600)
  },
  { immediate: true }
)
</script>

<template>
  <div ref="scroller" class="ot-carea">
    <div
      ref="gutter"
      class="ot-carea__gutter"
      :style="{ width: `calc(${String(lines.length).length}ch + 20px)` }"
      aria-hidden="true"
    >
      <span v-for="(_, i) in lines" :key="i">{{ i + 1 }}</span>
    </div>
    <div class="ot-carea__body">
      <div
        v-if="band"
        class="ot-carea__band"
        :style="{ top: `${(band.start - 1) * 1.55}em`, height: `${(band.end - band.start + 1) * 1.55}em` }"
        aria-hidden="true"
      />
      <template v-for="g in tags ?? []" :key="g.key">
        <div
          class="ot-carea__tagband"
          :style="{
            top: `${(g.start - 1) * 1.55}em`,
            height: `${(g.end - g.start + 1) * 1.55}em`,
            borderColor: g.color,
            backgroundColor: `color-mix(in srgb, ${g.color} 9%, transparent)`,
          }"
          aria-hidden="true"
        />
        <button
          type="button"
          class="ot-carea__tag"
          :style="{ top: `${(g.start - 1) * 18.6 + 1}px`, left: `${tagRight}px`, color: g.color, borderColor: g.color }"
          :title="`${g.label}：点开那件事，看它改到哪了`"
          data-cheese-mark
          @click="emit('tag', g.key)"
        >
          {{ g.label }}
        </button>
      </template>
      <textarea
        ref="area"
        v-model="text"
        class="ot-carea__text"
        :rows="lines.length"
        :readonly="readonly"
        wrap="off"
        spellcheck="false"
        autocomplete="off"
        :aria-label="label"
        :style="{ width: `calc(${cols}ch + 12px)` }"
      />
      <span
        v-for="p in peers ?? []"
        :key="p.handle"
        class="ot-carea__peer"
        :class="{ 'is-top': p.line === 1 }"
        :style="{ top: `${(p.line - 1) * 1.55}em`, left: `${p.col}ch`, borderColor: p.color }"
        aria-hidden="true"
      >
        <span class="ot-carea__peername" :style="{ backgroundColor: p.color }">{{ p.name }}</span>
      </span>
    </div>
  </div>
</template>

<style scoped>
/* The same type and rhythm as the read-only view (ChangesDiff): 12px mono, 1.55, gutter, 12px gaps. */
.ot-carea {
  display: flex;
  flex: 1;
  align-items: flex-start;
  min-height: 0;
  padding: 6px 0;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
}

.ot-carea__gutter {
  display: flex;
  flex: none;
  flex-direction: column;
  color: var(--faint);
  text-align: right;
  font-variant-numeric: tabular-nums;
  user-select: none;
}

/* The read-only view prints a diff marker column (a space) before each line: start where its text starts. */
.ot-carea__body {
  position: relative;
  flex: 1 0 auto;
  min-width: 0;
  margin: 0 0 0 19px;
}

.ot-carea__text {
  display: block;
  min-width: 100%;
  padding: 0 12px 0 0;
  margin: 0;
  overflow: hidden;
  font: inherit;
  line-height: inherit;
  color: var(--text);
  white-space: pre;
  resize: none;
  background: none;
  border: 0;
  outline: none;
  caret-color: var(--ink);
}

.ot-carea__text[readonly] {
  caret-color: transparent;
}

.ot-carea__band {
  position: absolute;
  right: 0;
  left: -19px;
  pointer-events: none;
  background: var(--accent-wash);
  border-left: 2px solid var(--accent-ink);
  animation: ot-band 2.6s var(--ease-out) forwards;
}

@keyframes ot-band {
  70% {
    opacity: 1;
  }

  100% {
    opacity: 0;
  }
}

/* A 一件事 changing these lines: a tinted band with a rule in 芝士's colour, and its tag at the right. */
.ot-carea__tagband {
  position: absolute;
  right: 0;
  left: -19px;
  pointer-events: none;
  border-left: 2px solid;
}

.ot-carea__tag {
  position: absolute;
  transform: translateX(-100%);
  z-index: 1;
  height: 17px;
  padding: 0 6px;
  font-family: var(--font-sans);
  font-size: 11px;
  line-height: 15px;
  white-space: nowrap;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid;
  border-radius: var(--radius-sm);
}

/* A teammate's caret: the documents' caret (DocProse), a line in their colour with their name above it. */
.ot-carea__peer {
  position: absolute;
  height: 1.55em;
  pointer-events: none;
  border-left: 2px solid;
  transition:
    top var(--dur-base) var(--ease-out),
    left var(--dur-base) var(--ease-out);
}

.ot-carea__peername {
  position: absolute;
  bottom: 100%;
  left: -2px;
  padding: 0 4px;
  font-family: var(--font-sans);
  font-size: 11px;
  font-weight: 600;
  line-height: 16px;
  color: #fff;
  white-space: nowrap;
  border-radius: 3px 3px 3px 0;
}

/* On the first line the name would sit above the text and be cut off: it goes under the caret. */
.ot-carea__peer.is-top .ot-carea__peername {
  top: 100%;
  bottom: auto;
  border-radius: 0 3px 3px;
}

@media (prefers-reduced-motion: reduce) {
  .ot-carea__band {
    animation: none;
  }

  .ot-carea__peer {
    transition: none;
  }
}
</style>
