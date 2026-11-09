<script setup lang="ts">
// A conversation drawn the way the product's ChatTimeline draws it: a day line
// whenever the day changes, one person's run of messages under one name
// (the product's own grouping rules, lib/chatGrouping), the hover bar on the
// row under the pointer, reactions, a reply's quote, and a 一件事's card under
// the message that opened it.
import type { Block } from '@/cx_types'
import type { ChatLine, MarkLine } from './state'

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'

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
// Rows grow after mount (markdown renders lazily), so settle at the end once more after they do.
onMounted(() => {
  void toEnd('instant')
  setTimeout(() => void toEnd('instant'), 400)
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
  <!-- eslint-disable-next-line vuejs-accessibility/mouse-events-have-key-events, vuejs-accessibility/no-static-element-interactions -->
  <div ref="content" class="ot-tl" @mouseover="hover" @mouseleave="leave">
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
