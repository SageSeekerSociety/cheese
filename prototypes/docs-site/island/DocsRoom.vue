<script setup lang="ts">
import type { Block } from '@/cx_types'
import type { SplitMarker } from '@/lib/splitMarkers'
import type { Ref } from 'vue'

import { computed } from 'vue'

import DispatchedMarker from '@/components/DispatchedMarker.vue'
import RoomMessage from '@/components/room/RoomMessage.vue'
import TimelineMark from '@/components/TimelineMark.vue'

const props = defineProps<{ step: Ref<number> }>()
const step = computed(() => props.step.value)

const AGENT = 'cheese'
const refs = { mentionNames: { cheese: '芝士' }, topicTitles: {} }
const names: Record<string, string> = { wang: '小王', li: '李老师', [AGENT]: '芝士' }

type Line =
  | { at: number; kind: 'message'; id: string; author: string; text: string; time: string; artifact?: boolean }
  | { at: number; kind: 'marker'; id: string; title: string; doneAt: number }
  | { at: number; kind: 'day'; id: string; label: string }

const lines: Line[] = [
  { at: 0, kind: 'day', id: 'd1', label: '今天' },
  { at: 0, kind: 'message', id: 'm1', author: 'wang', time: '09:12', text: '<@cheese> 把这周三份会议纪要整理成一页周报：按项目分节，结尾列待办，下午三点前要。' },
  { at: 0, kind: 'message', id: 'm2', author: AGENT, time: '09:12', text: '明白：一页、按项目分节、结尾是待办和负责人。我先读三份纪要，拆成三件事同时做。' },
  { at: 1, kind: 'marker', id: 'k1', title: '读三份会议纪要，按项目归类', doneAt: 1 },
  { at: 1, kind: 'marker', id: 'k2', title: '起草周报正文', doneAt: 2 },
  { at: 1, kind: 'marker', id: 'k3', title: '核对每条待办的负责人和出处', doneAt: 3 },
  { at: 2, kind: 'message', id: 'm3', author: AGENT, time: '09:31', text: 'weekly-report.docx', artifact: true },
  { at: 2, kind: 'message', id: 'm4', author: 'li', time: '09:34', text: '标题用本周日期，待办按截止时间排。' },
  { at: 3, kind: 'message', id: 'm5', author: AGENT, time: '09:36', text: '改好了：标题是「9 月 22 日–26 日周报」，9 条待办按截止时间排好，每条都标了出处。请验收。' },
]

const shown = computed(() => lines.filter((l) => l.at <= step.value))

function block(line: Extract<Line, { kind: 'message' }>): Block {
  return {
    id: line.id,
    topic_id: 'demo',
    kind: line.artifact ? 'artifact' : 'message',
    mime_type: line.artifact ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' : null,
    author_type: 'participant',
    author: line.author,
    content: line.text,
    created_at: '',
  } as Block
}
function marker(line: Extract<Line, { kind: 'marker' }>): SplitMarker {
  return { taskId: line.id, title: line.title, status: step.value >= line.doneAt ? 'closed' : 'open', createdAt: '' }
}
function runStart(i: number) {
  const line = shown.value[i], prev = shown.value[i - 1]
  return line.kind !== 'message' || !prev || prev.kind !== 'message' || prev.author !== line.author
}
</script>

<template>
  <div class="room" :data-step="step">
    <div class="room-bar">
      <span class="room-bar-project">市场部</span>
      <span class="room-bar-topic"># 本周周报</span>
      <span class="room-bar-sample">示例</span>
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
              :mine="line.author === 'wang'"
              :topic-id="null"
              :author-name="names[line.author]"
              :avatar="null"
              :is-agent="line.author === AGENT"
              :time="line.time"
              :refs="refs"
              viewer="wang"
              :ask-busy="false"
            />
          </template>
        </TransitionGroup>
        <div class="room-composer">说点什么，@ 芝士让它干活</div>
      </div>
      <Transition name="room-pane">
        <aside v-if="step >= 2" class="room-preview" inert>
          <div class="room-preview-bar"><v-icon size="16">mdi-file-word-outline</v-icon>weekly-report.docx</div>
          <div class="room-preview-page">
            <b class="doc-title">{{ step >= 3 ? '9 月 22 日–26 日周报' : '本周周报' }}</b>
            <div class="doc-sec"><i>官网改版</i><span /><span class="w80" /></div>
            <div class="doc-sec"><i>校园活动</i><span /><span class="w60" /></div>
            <div class="doc-sec"><i>客户回访</i><span class="w90" /><span class="w50" /></div>
            <div class="doc-todo"><i>待办 · 9 条</i><span v-for="n in 4" :key="n" class="w70" /></div>
          </div>
          <Transition name="room-pane">
            <div v-if="step >= 3" class="room-review">
              <span class="room-review-text">等你验收</span>
              <v-btn size="small" variant="outlined" tabindex="-1">退回</v-btn>
              <v-btn size="small" variant="flat" color="primary" tabindex="-1">采纳</v-btn>
            </div>
          </Transition>
        </aside>
      </Transition>
    </div>
  </div>
</template>

<style>
html, body { margin: 0; height: 100%; background: transparent; }
#room { height: 100%; }
</style>

<style scoped>
.room { display: flex; flex-direction: column; height: 100%; overflow: hidden; background: var(--surface); border: 1px solid var(--line-2); border-radius: var(--radius-lg); }
.room-bar { display: flex; flex: none; align-items: center; gap: 12px; height: 48px; padding: 0 16px; font-size: 14px; border-bottom: 1px solid var(--line); }
.room-bar-project { font-weight: 600; color: var(--ink); }
.room-bar-topic { color: var(--muted); }
.room-bar-sample { margin-left: auto; font-size: 12px; color: var(--faint); }
.room-body { display: flex; flex: 1; min-height: 0; }
.room-chat { display: flex; flex: 1; flex-direction: column; min-width: 0; padding: 16px 0; }
.room-lines { display: flex; flex: 1; flex-direction: column; justify-content: flex-end; overflow: hidden; }
.room-composer { display: flex; align-items: center; height: 44px; padding: 0 12px; margin: 12px 16px 0; font-size: 14px; color: var(--faint); border: 1px solid var(--line-2); border-radius: var(--radius-md); }
.room-preview { display: flex; flex: none; flex-direction: column; width: 42%; background: var(--fill); border-left: 1px solid var(--line); }
.room-preview-bar { display: flex; align-items: center; gap: 6px; height: 40px; padding: 0 16px; font-size: 13px; color: var(--muted); border-bottom: 1px solid var(--line); }
.room-preview-page { display: flex; flex: 1; flex-direction: column; gap: 14px; padding: 22px; margin: 16px; background: var(--surface); border: 1px solid var(--line-2); border-radius: var(--radius-lg); }
.doc-title { font-size: 15px; color: var(--ink); }
.doc-sec, .doc-todo { display: flex; flex-direction: column; gap: 6px; }
.doc-sec i, .doc-todo i { font-style: normal; font-size: 12px; color: var(--muted); }
.doc-sec span, .doc-todo span { display: block; height: 7px; width: 100%; border-radius: 4px; background: var(--fill-2); }
.w50 { width: 50% !important; } .w60 { width: 60% !important; } .w70 { width: 70% !important; } .w80 { width: 80% !important; } .w90 { width: 90% !important; }
.room-review { display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--line); background: var(--surface); }
.room-review-text { flex: 1; font-size: 13px; color: var(--muted); }
.room-line-enter-active { transition: opacity .45s ease, transform .45s cubic-bezier(.16,1,.3,1); }
.room-line-enter-from { opacity: 0; transform: translateY(12px); }
.room-line-leave-active { display: none; }
.room-pane-enter-active { transition: opacity .5s ease, transform .5s cubic-bezier(.16,1,.3,1); }
.room-pane-enter-from { opacity: 0; transform: translateX(24px); }
.room-pane-leave-active { transition: opacity .2s ease; }
.room-pane-leave-to { opacity: 0; }
</style>
