<script setup lang="ts">
// A conversation drawn the way the product's ChatTimeline draws it: a day line
// whenever the day changes, one person's run of messages under one name
// (the product's own grouping rules, lib/chatGrouping), the hover bar on the
// row under the pointer, reactions, a reply's quote, and a 一件事's card under
// the message that opened it.
import type { Block } from '@/cx_types'
import type { ChatLine, MarkLine } from './state'

import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { ME, NAMES } from './data'
import { block, hhmm, ownerName, REFS, taskLine } from './shapes'
import { openThing, react, S, thingById, toast } from './state'

import RoomHoverBar from '@/components/room/RoomHoverBar.vue'
import RoomMessage from '@/components/room/RoomMessage.vue'
import TaskCard from '@/components/room/TaskCard.vue'
import TimelineMark from '@/components/TimelineMark.vue'
import { dayLabelsFor, runEdgeBetween } from '@/lib/chatGrouping'

const props = defineProps<{ lines: (ChatLine | MarkLine)[]; conversation: string; cards?: boolean }>()
const emit = defineEmits<{ (e: 'reply', line: ChatLine): void }>()

const said = (l: ChatLine | MarkLine): l is ChatLine => 'who' in l

const blocks = computed(() =>
  props.lines.map((l) =>
    said(l)
      ? block(l, props.conversation)
      : ({
          id: l.id,
          kind: 'event',
          author: 'system',
          author_type: 'platform',
          content: l.mark,
          created_at: l.at,
        } as Block)
  )
)
const days = computed(() => dayLabelsFor(blocks.value.map((b) => ({ block: b }))))
const edges = computed(() =>
  blocks.value.map((b, i) => {
    const prev = blocks.value[i - 1]
    // A day line or a card between two messages breaks the run, as in the product.
    const broken = days.value.has(b.id) || (i > 0 && !!(props.lines[i - 1] as ChatLine).thing && !!props.cards)
    return runEdgeBetween(prev, b, { broken })
  })
)
const byId = computed(() => new Map(props.lines.filter(said).map((l) => [l.id, l])))

// ---- the hover bar ----
const content = ref<HTMLElement | null>(null)
const bar = reactive({ id: null as string | null, shown: false, top: 0, jump: true })
const picker = ref<string | null>(null)
const barBlock = computed(() => blocks.value.find((b) => b.id === bar.id && b.kind === 'message') ?? null)

function hover(e: MouseEvent) {
  const row = (e.target as HTMLElement).closest<HTMLElement>('[data-line]')
  if (!row) return
  const id = row.dataset.line!
  if (!byId.value.has(id)) return
  if (bar.id !== id) picker.value = null
  bar.jump = !bar.shown
  bar.id = id
  bar.top = row.offsetTop
  bar.shown = true
}
// A person's face or name opens who they are, as the product's name card does.
function person(e: MouseEvent) {
  const el = (e.target as HTMLElement).closest<HTMLElement>('.im-person')
  if (!el) return
  const row = el.closest<HTMLElement>('[data-line]')
  const who = row ? byId.value.get(row.dataset.line!)?.who : undefined
  if (who) toast(`${NAMES[who]} · @${who}${who === 'cheese' ? ' · AI 队友' : ''}`)
}
function leave() {
  bar.shown = false
  picker.value = null
}

function onReact(b: Block, emoji: string) {
  react(b.id, emoji)
  picker.value = null
}
function onReply(b: Block) {
  const line = byId.value.get(b.id)
  if (line) emit('reply', line)
}

const parentOf = (l: ChatLine) => (l.replyTo ? blocks.value.find((b) => b.id === l.replyTo) ?? null : null)

// Opens at the newest message, like the product; a new one scrolls into view.
async function toEnd(behavior: 'instant' | 'smooth') {
  await nextTick()
  const scroller = content.value?.parentElement
  scroller?.scrollTo({ top: scroller.scrollHeight, behavior })
}
// The scroller narrows or widens when the right panel opens or closes, and the
// rows rewrap to a new height. Keep what the reader was looking at where it was:
// the newest message if they were at the end, otherwise the first row in view
// at the same height on screen.
let anchor = { atEnd: true, id: null as string | null, off: 0 }
const scrollerOf = () => content.value?.parentElement ?? null
// restore()'s own scroll event lands a frame later, when the panel's open
// animation has already made the rows taller again: reading it would record
// "no longer at the end". rAF set in a ResizeObserver callback runs after the
// next frame's scroll events, so the flag covers exactly that event.
let own = false
function remember() {
  const sc = scrollerOf()
  if (!sc || own) return
  const top = sc.getBoundingClientRect().top
  const row = [...sc.querySelectorAll<HTMLElement>('[data-line]')].find((r) => r.getBoundingClientRect().bottom > top)
  anchor = {
    atEnd: sc.scrollHeight - sc.scrollTop - sc.clientHeight < 8,
    id: row?.dataset.line ?? null,
    off: row ? row.getBoundingClientRect().top - top : 0,
  }
}
function restore() {
  const sc = scrollerOf()
  if (!sc) return
  own = true
  requestAnimationFrame(() => (own = false))
  if (anchor.atEnd) {
    sc.scrollTop = sc.scrollHeight
    return
  }
  const row = anchor.id ? sc.querySelector<HTMLElement>(`[data-line="${CSS.escape(anchor.id)}"]`) : null
  if (row) sc.scrollTop += row.getBoundingClientRect().top - sc.getBoundingClientRect().top - anchor.off
}
let ro: ResizeObserver | null = null

// A channel 文档's ↗ and a 一件事's 来自 ask for one message: if it is in this timeline,
// centre it in the scroller (not scrollIntoView, which would also scroll the
// clipped workspace around it) and flash the row.
function jumpHere(): boolean {
  const sc = scrollerOf()
  const row = S.jumpId ? content.value?.querySelector<HTMLElement>(`[data-line="${CSS.escape(S.jumpId)}"]`) : null
  if (!sc || !row) return false
  S.jumpId = null
  const top = row.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop
  sc.scrollTo({ top: Math.max(0, top - (sc.clientHeight - row.offsetHeight) / 2), behavior: 'smooth' })
  row.classList.remove('ot-line-flash')
  void row.offsetWidth
  row.classList.add('ot-line-flash')
  setTimeout(() => row.classList.remove('ot-line-flash'), 2300)
  return true
}
watch(
  () => S.jumpTick,
  () => void nextTick(jumpHere)
)

// Rows grow after mount (markdown renders lazily), so settle at the end once more after they do,
// or, when this timeline was opened to show one message, show that one once they have.
onMounted(() => {
  void toEnd('instant')
  setTimeout(() => jumpHere() || void toEnd('instant'), 400)
  const sc = scrollerOf()
  if (!sc || !content.value) return
  // The browser's own scroll anchoring would move the view first and leave remember() a moved position.
  sc.style.overflowAnchor = 'none'
  sc.addEventListener('scroll', remember, { passive: true })
  // The rows rewrap (content) and the composer under the scroller can change height (scroller).
  ro = new ResizeObserver(restore)
  ro.observe(content.value)
  ro.observe(sc)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  scrollerOf()?.removeEventListener('scroll', remember)
})
watch(
  () => props.conversation,
  () => toEnd('instant')
)
watch(
  () => props.lines.length,
  () => toEnd('smooth')
)
</script>

<template>
  <!-- The hover bar follows the pointer, as in the product timeline; keyboard users reach the same actions on the row itself. -->
  <!-- eslint-disable-next-line vuejs-accessibility/mouse-events-have-key-events, vuejs-accessibility/no-static-element-interactions, vuejs-accessibility/click-events-have-key-events -->
  <div ref="content" class="ot-tl" @mouseover="hover" @mouseleave="leave" @click="person">
    <RoomHoverBar
      :block="barBlock"
      :shown="bar.shown && !!barBlock"
      :top="bar.top"
      :jump="bar.jump"
      :is-agent="barBlock?.author === 'cheese'"
      :picker-open="!!barBlock && picker === barBlock.id"
      :editable="false"
      no-upgrade
      :threadable="false"
      :processable="false"
      :pinnable="cards"
      @react="onReact"
      @toggle-picker="picker = picker === $event ? null : $event"
      @reply="onReply"
      @pin="toast('已置顶到频道')"
      @unpin="toast('已取消置顶')"
    />
    <template v-for="(line, i) in lines" :key="line.id">
      <TimelineMark v-if="days.has(line.id)" quiet>{{ days.get(line.id) }}</TimelineMark>
      <div v-if="said(line)" class="ot-tl__line" :data-line="line.id">
        <RoomMessage
          :block="blocks[i]!"
          :parent="parentOf(line)"
          :parent-name="line.replyTo ? NAMES[byId.get(line.replyTo)?.who ?? ''] ?? null : null"
          :run-start="edges[i] !== 'cont'"
          :regroup="edges[i] === 'regroup'"
          :mine="line.who === ME"
          :topic-id="null"
          :author-name="NAMES[line.who] ?? line.who"
          :avatar="null"
          :is-agent="line.who === 'cheese'"
          :time="hhmm(line.at)"
          :refs="REFS"
          :viewer="ME"
          :ask-busy="false"
          :active="bar.shown && bar.id === line.id"
          @react="onReact"
        />
        <div v-if="cards && line.thing && thingById(line.thing)" class="ot-tl__card">
          <TaskCard
            :id="`card-${line.thing}`"
            :class="{ 'ot-sel': S.thing === line.thing, 'ot-flash': S.morph && line.thing === 't4' }"
            :task="taskLine(thingById(line.thing)!)"
            :owner-name="ownerName(thingById(line.thing)!)"
            @open="openThing"
          />
        </div>
      </div>
      <div v-else class="ot-tl__mark" :class="{ 'ot-tl__mark--ok': line.ok }">
        <v-icon size="14" :icon="line.ok ? 'mdi-check-circle-outline' : 'mdi-play-circle-outline'" />
        {{ line.mark }}<span class="ot-tl__mark-at">{{ hhmm(line.at) }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ot-tl {
  position: relative;
  width: 100%;
  container: chat-timeline / inline-size;
}

.ot-tl__card {
  /* Under the message text: lines up with the message body, past the avatar column. */
  padding: 2px 16px 8px 60px;
}

.ot-tl__mark {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: center;
  padding: 10px 16px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-tl__mark--ok {
  color: var(--ok-ink);
}

.ot-tl__mark-at {
  margin-left: 4px;
  font-family: var(--font-mono);
  color: var(--faint);
}
</style>
