<script setup lang="ts">
import type { Block } from '@/cx_types'
import type { SplitMarker } from '@/lib/splitMarkers'

import { computed } from 'vue'

import DispatchedMarker from '@/components/DispatchedMarker.vue'
import RoomMessage from '@/components/room/RoomMessage.vue'
import TimelineMark from '@/components/TimelineMark.vue'
import { t } from '@/i18n'

// The room on the public site: the workbench's own message rows fed a scripted
// project, so the demo looks like the product because it is the product's rows.
// `step` is how far the story has scrolled; each step adds what happened next.

const props = defineProps<{ step: number }>()

const AGENT = 'cheese'
const refs = { mentionNames: {}, topicTitles: {} }

type Line =
  | { at: number; kind: 'message'; id: string; author: string; text: string; time: string; artifact?: boolean }
  | { at: number; kind: 'marker'; id: string; title: string; doneAt: number }
  | { at: number; kind: 'day'; id: string; label: string }

const lines = computed<Line[]>(() => [
  { at: 0, kind: 'day', id: 'd1', label: t('publicSite.room.dayEarlier') },
  { at: 0, kind: 'message', id: 'm0', author: 'li', text: t('publicSite.room.setCriteria'), time: '16:20' },
  { at: 0, kind: 'message', id: 'm0b', author: AGENT, text: t('publicSite.room.noted'), time: '16:21' },
  { at: 0, kind: 'day', id: 'd2', label: t('publicSite.room.dayToday') },
  { at: 0, kind: 'message', id: 'm1', author: 'wang', text: t('publicSite.room.askProgress'), time: '09:41' },
  { at: 0, kind: 'message', id: 'm2', author: AGENT, text: t('publicSite.room.answerProgress'), time: '09:41' },
  { at: 1, kind: 'message', id: 'm3', author: 'li', text: t('publicSite.room.askPrototype'), time: '14:02' },
  { at: 1, kind: 'message', id: 'm4', author: AGENT, text: t('publicSite.room.splitWork'), time: '14:03' },
  { at: 1, kind: 'marker', id: 'k1', title: t('publicSite.room.taskSources'), doneAt: 1 },
  { at: 1, kind: 'marker', id: 'k2', title: t('publicSite.room.taskPrototype'), doneAt: 2 },
  { at: 1, kind: 'marker', id: 'k3', title: t('publicSite.room.taskReport'), doneAt: 3 },
  {
    at: 2,
    kind: 'message',
    id: 'm5',
    author: AGENT,
    text: t('publicSite.room.prototypeFile'),
    time: '15:08',
    artifact: true,
  },
  { at: 3, kind: 'message', id: 'm6', author: 'wang', text: t('publicSite.room.triedIt'), time: '15:12' },
])

const names = computed<Record<string, string>>(() => ({
  wang: t('publicSite.room.wang'),
  li: t('publicSite.room.li'),
  [AGENT]: t('publicSite.room.agent'),
}))

const shown = computed(() => lines.value.filter((line) => line.at <= props.step))

function block(line: Extract<Line, { kind: 'message' }>): Block {
  return {
    id: line.id,
    topic_id: 'demo',
    kind: line.artifact ? 'artifact' : 'message',
    author_type: 'participant',
    author: line.author,
    content: line.text,
    created_at: '',
  }
}

function marker(line: Extract<Line, { kind: 'marker' }>): SplitMarker {
  return { taskId: line.id, title: line.title, status: props.step >= line.doneAt ? 'closed' : 'open', createdAt: '' }
}

function runStart(index: number) {
  const line = shown.value[index]
  const prev = shown.value[index - 1]
  return line.kind !== 'message' || !prev || prev.kind !== 'message' || prev.author !== line.author
}
</script>

<template>
  <div class="room" :data-step="step">
    <div class="room-bar">
      <span class="room-bar-project">{{ t('publicSite.room.project') }}</span>
      <span class="room-bar-topic"># {{ t('publicSite.room.topic') }}</span>
      <span class="room-bar-sample">{{ t('publicSite.room.sample') }}</span>
    </div>
    <div class="room-body">
      <div class="room-chat" inert>
        <TransitionGroup name="room-line" tag="div" class="room-lines">
          <template v-for="(line, i) in shown" :key="line.id">
            <TimelineMark v-if="line.kind === 'day'" quiet>{{ line.label }}</TimelineMark>
            <DispatchedMarker v-else-if="line.kind === 'marker'" :marker="marker(line)" />
            <RoomMessage
              v-else
              :block="block(line)"
              :parent="null"
              :parent-name="null"
              :run-start="runStart(i)"
              :mine="false"
              :topic-id="null"
              :author-name="names[line.author]"
              :avatar="null"
              :is-agent="line.author === AGENT"
              :time="line.time"
              :refs="refs"
              viewer=""
              :picker-open="false"
              :ask-busy="false"
              :summon-hint="null"
              :summon-busy="false"
            />
          </template>
        </TransitionGroup>
        <div class="room-composer">{{ t('publicSite.room.composer') }}</div>
      </div>
      <Transition name="room-pane">
        <aside v-if="step >= 2" class="room-preview" inert>
          <div class="room-preview-bar">{{ t('publicSite.room.previewTitle') }}</div>
          <div class="room-preview-page">
            <p class="room-preview-eyebrow">{{ t('publicSite.room.previewApp') }}</p>
            <div class="room-preview-query">{{ t('publicSite.room.previewQuestion') }}</div>
            <p class="room-preview-answer">{{ t('publicSite.room.previewAnswer') }}</p>
            <div class="room-preview-cites">
              <span>{{ t('publicSite.room.citeGuide') }}</span>
              <span>{{ t('publicSite.room.citeMeeting') }}</span>
            </div>
          </div>
          <Transition name="room-pane">
            <div v-if="step >= 3" class="room-review">
              <span class="room-review-text">{{ t('publicSite.room.reviewState') }}</span>
              <v-btn size="small" variant="outlined" tabindex="-1">{{ t('publicSite.room.sendBack') }}</v-btn>
              <v-btn size="small" variant="flat" color="primary" tabindex="-1">{{ t('publicSite.room.adopt') }}</v-btn>
            </div>
          </Transition>
        </aside>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.room {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}

.room-bar {
  display: flex;
  flex: none;
  align-items: center;
  gap: 12px;
  height: 48px;
  padding: 0 16px;
  font-size: 14px;
  line-height: var(--lh-14);
  border-bottom: 1px solid var(--line);
}

.room-bar-project {
  font-weight: 600;
  color: var(--ink);
}

.room-bar-topic {
  color: var(--muted);
}

.room-bar-sample {
  margin-left: auto;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.room-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.room-chat {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  padding: 16px 0;
}

.room-lines {
  display: flex;
  flex: 1;
  flex-direction: column;
  justify-content: flex-end;
  overflow: hidden;
}

.room-composer {
  display: flex;
  height: 44px;
  padding: 0 12px;
  margin: 12px 16px 0;
  font-size: 14px;
  color: var(--faint);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  align-items: center;
}

.room-preview {
  display: flex;
  flex: none;
  flex-direction: column;
  width: 44%;
  background: var(--fill);
  border-left: 1px solid var(--line);
}

.room-preview-bar {
  display: flex;
  align-items: center;
  height: 40px;
  padding: 0 16px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  border-bottom: 1px solid var(--line);
}

.room-preview-page {
  display: flex;
  padding: 24px;
  margin: 16px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  flex: 1;
  flex-direction: column;
  gap: 12px;
}

.room-preview-eyebrow {
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.room-preview-query {
  padding: 8px 12px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.room-preview-answer {
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14-loose);
  color: var(--text);
}

.room-preview-cites {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.room-preview-cites span {
  padding: 2px 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill-2);
  border-radius: var(--radius-sm);
}

.room-review {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: var(--surface);
  border-top: 1px solid var(--line);
}

.room-review-text {
  flex: 1;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.room-line-enter-active,
.room-pane-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}

.room-line-leave-active,
.room-pane-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}

.room-line-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.room-pane-enter-from {
  opacity: 0;
  transform: translateX(12px);
}

.room-line-leave-to,
.room-pane-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .room-line-enter-active,
  .room-line-leave-active,
  .room-pane-enter-active,
  .room-pane-leave-active {
    transition: none;
  }
}
</style>
