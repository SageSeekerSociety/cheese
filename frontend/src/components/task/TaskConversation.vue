<script setup lang="ts">
// 任务自己的对话：负责人和做这个任务的 AI 队友说的话，和话与话之间它做过的事。
//
// 只有负责人能在这里说话。别人打开任务看得到全部，但输入框的位置换成一句说明和回到
// 房间的入口——对这个任务有意见，到房间里说。数据全由上层给，这里不取数。
import type { Block } from '../../cx_types'
import type { RefNames } from '../../lib/refChip'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { useStickToBottom } from '../../composables/useStickToBottom'
import { isAgentBlock, isAgentHandle } from '../../lib/authorship'
import { noticeText } from '../../lib/noticeText'
import { type PlatformNotice, platformNotice } from '../../lib/platformNotice'
import { relTime } from '../../lib/relTime'
import { editableText, renderPlain } from '../../lib/renderMessage'
import { eventArg, eventFailed, eventVerb, isNarration } from '../../lib/siteLog'
import { myHandle } from '../../me'
import MarkdownView from '../common/MarkdownView.vue'
import MessageEditor from '../room/MessageEditor.vue'
import RoomNotice from '../room/RoomNotice.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    blocks: Block[]
    /** 输入框里的草稿，由上层持有：发送失败时它还在。 */
    draft: string
    /** 为什么不能在这里说话；null 表示能说（看的人是负责人，任务也没关）。 */
    blocked?: 'not-owner' | 'closed' | null
    /** 任务所在房间的名字，回到房间的入口上用。 */
    roomTitle: string
    agentName: string
    /** handle → 名字。 */
    memberNames?: Record<string, string>
    sending?: boolean
    sendError?: string | null
    editSaving?: boolean
    editError?: string | null
    /** 打开时停在这一条。 */
    focusBlock?: string | null
  }>(),
  {
    blocked: null,
    memberNames: () => ({}),
    sending: false,
    sendError: null,
    editSaving: false,
    editError: null,
    focusBlock: null,
  }
)

const emit = defineEmits<{
  (e: 'update:draft', text: string): void
  (e: 'send', text: string): void
  (e: 'save-edit', block: Block, text: string): void
  (e: 'open-room'): void
}>()

const timelineRef = ref<HTMLElement | null>(null)
useStickToBottom(timelineRef, 80)

type Entry =
  | { kind: 'say'; block: Block }
  | { kind: 'notice'; block: Block; notice: PlatformNotice }
  | { kind: 'steps'; key: string; blocks: Block[] }

// 两句话之间连着的几步操作并成一行「N 步操作」，默认折着，点开是流水账。AI 队友
// 自言自语的那种事件是话，不是操作。
const entries = computed<Entry[]>(() => {
  const out: Entry[] = []
  for (const b of props.blocks) {
    const notice = b.meta?.event_type && !b.meta?.tool ? platformNotice(b) : null
    if (notice && !['hidden', 'action', 'turn-summary'].includes(notice.mode)) {
      out.push({ kind: 'notice', block: b, notice })
    } else if (notice?.mode === 'hidden') {
      continue
    } else if (b.kind === 'event' && !isNarration(b.meta)) {
      const last = out[out.length - 1]
      if (last?.kind === 'steps') last.blocks.push(b)
      else out.push({ kind: 'steps', key: b.id, blocks: [b] })
    } else if ((b.kind === 'message' || b.kind === 'event') && (b.content || '').trim()) {
      out.push({ kind: 'say', block: b })
    }
  }
  return out
})

const openSteps = ref(new Set<string>())
function toggleSteps(key: string) {
  const next = new Set(openSteps.value)
  if (!next.delete(key)) next.add(key)
  openSteps.value = next
}

const refs = computed<RefNames>(() => ({ mentionNames: props.memberNames, topicTitles: {} }))

function whoSaid(b: Block): string {
  return props.memberNames[b.author] || (isAgentHandle(b.author) ? props.agentName : b.author)
}

const editingId = ref<string | null>(null)
function canEdit(b: Block): boolean {
  return props.blocked === null && b.kind === 'message' && b.author === myHandle() && !isAgentBlock(b)
}
function saveEdit(b: Block, text: string) {
  const content = text.trim()
  if (!content || content === editableText(b.content, refs.value).trim()) {
    editingId.value = null
    return
  }
  emit('save-edit', b, content)
}
// 上层存好了（块被换成新的那一份）就收起编辑框。
watch(
  () => props.blocks,
  (blocks) => {
    if (!editingId.value) return
    const edited = blocks.find((b) => b.id === editingId.value)
    if (edited?.meta?.edited_at && !props.editSaving && !props.editError) editingId.value = null
  }
)

// 停到一条上，并让它闪一下。
const flashId = ref<string | null>(null)
let flashTimer: ReturnType<typeof setTimeout> | undefined
function showBlock(id: string): boolean {
  const row = timelineRef.value?.querySelector(`[data-mid="${id}"]`)
  if (!row) return false
  row.scrollIntoView({ block: 'center' })
  clearTimeout(flashTimer)
  flashId.value = id
  flashTimer = setTimeout(() => (flashId.value = null), 1600)
  return true
}
onBeforeUnmount(() => clearTimeout(flashTimer))
// 每个点名的那一条只停一次：它落进列表时（可能晚于打开）停过去，之后新来的消息
// 不再把人拽回它。
let shown: string | null = null
watch(
  () => [props.focusBlock, props.blocks.length] as const,
  ([id]) => {
    if (!id || id === shown) return
    void nextTick(() => {
      if (showBlock(id)) shown = id
    })
  },
  { immediate: true }
)

function send() {
  const text = props.draft.trim()
  if (!text || props.sending || props.blocked) return
  emit('send', text)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}
</script>

<template>
  <section class="task-chat">
    <div ref="timelineRef" class="task-chat__timeline" data-testid="task-timeline">
      <div v-if="!entries.length" class="task-chat__empty t-meta">{{ t('work.task.noMessages') }}</div>
      <template v-for="e in entries" :key="e.kind === 'steps' ? e.key : e.block.id">
        <div
          v-if="e.kind === 'say'"
          class="task-msg"
          :class="{ 'task-msg--flash': flashId === e.block.id }"
          :data-mid="e.block.id"
        >
          <div class="task-msg__head">
            <span class="task-msg__who t-meta">{{ whoSaid(e.block) }}</span>
            <span class="task-msg__time t-meta">{{ relTime(e.block.created_at) }}</span>
            <button
              v-if="canEdit(e.block) && editingId !== e.block.id"
              type="button"
              class="task-msg__edit t-meta"
              @click="editingId = e.block.id"
            >
              {{ t('work.room.message.edit') }}
            </button>
          </div>
          <MessageEditor
            v-if="editingId === e.block.id"
            :text="editableText(e.block.content, refs)"
            :saving="editSaving"
            @save="saveEdit(e.block, $event)"
            @cancel="editingId = null"
          />
          <MarkdownView
            v-else-if="isAgentBlock(e.block)"
            class="task-msg__text task-markdown t-body"
            :source="e.block.content"
            as="chat"
            :names="refs"
          />
          <span
            v-else
            class="task-msg__text t-body"
            v-html="renderPlain(e.block.kind === 'event' ? noticeText(e.block) : e.block.content, refs)"
          />
          <span v-if="e.block.meta?.edited_at && editingId !== e.block.id" class="task-msg__edited">{{
            t('work.room.message.edited')
          }}</span>
          <span v-if="editingId === e.block.id && editError" class="task-msg__error" role="alert">{{ editError }}</span>
        </div>
        <RoomNotice
          v-else-if="e.kind === 'notice'"
          :block="e.block"
          :notice="e.notice"
          :run="[e.block]"
          :agent="null"
          :time="relTime(e.block.created_at)"
          :agent-name="agentName"
          :refs="refs"
        />
        <div v-else class="task-steps">
          <button
            type="button"
            class="task-steps__head t-meta"
            :aria-expanded="openSteps.has(e.key)"
            @click="toggleSteps(e.key)"
          >
            <v-icon size="14">{{ openSteps.has(e.key) ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
            <span>{{ t('work.room.card.steps', { count: e.blocks.length }) }}</span>
            <span v-if="e.blocks.some(eventFailed)" class="task-steps__failed">{{
              t('work.room.card.stepFailed')
            }}</span>
          </button>
          <ol v-if="openSteps.has(e.key)" class="task-steps__list">
            <li v-for="b in e.blocks" :key="b.id" class="task-step" :class="{ 'task-step--failed': eventFailed(b) }">
              <span class="task-step__verb">{{ eventVerb(b) }}</span>
              <span v-if="eventArg(b)" class="task-step__arg" :title="eventArg(b)">{{ eventArg(b) }}</span>
            </li>
          </ol>
        </div>
      </template>
    </div>

    <form v-if="!blocked" class="task-chat__say" data-testid="task-composer" @submit.prevent="send">
      <textarea
        :value="draft"
        rows="2"
        autocomplete="off"
        class="task-chat__input t-body"
        :placeholder="t('work.task.sayPlaceholder', { name: agentName })"
        :disabled="sending"
        @input="emit('update:draft', ($event.target as HTMLTextAreaElement).value)"
        @keydown="onKeydown"
      />
      <button type="submit" class="task-chat__send t-meta" :disabled="sending || !draft.trim()">
        {{ t('work.task.send') }}
      </button>
    </form>
    <div v-else class="task-chat__blocked t-meta" data-testid="task-blocked">
      <span>{{
        blocked === 'closed' ? t('work.task.closedNotice') : t('work.task.ownerOnlyNotice', { name: agentName })
      }}</span>
      <button v-if="blocked === 'not-owner'" type="button" class="task-chat__room" @click="emit('open-room')">
        {{ t('work.task.backToRoom', { room: roomTitle }) }}
      </button>
    </div>
    <p v-if="sendError" class="task-chat__error t-meta" role="alert">{{ sendError }}</p>
  </section>
</template>

<style scoped>
.task-chat {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}
.task-chat__timeline {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 16px;
}
.task-chat__empty {
  padding: 8px 0;
  color: var(--muted);
}
.task-msg {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 0;
}
.task-msg--flash {
  border-radius: var(--radius-sm);
  animation: task-msg-flash 1.6s var(--ease-out);
}
@keyframes task-msg-flash {
  from,
  25% {
    background-color: var(--accent-wash);
  }
}
.task-msg__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.task-msg__who {
  color: var(--text);
  font-weight: 600;
}
.task-msg__time {
  color: var(--faint);
}
.task-msg__edit {
  padding: 0;
  border: none;
  background: none;
  color: var(--muted);
  cursor: pointer;
  opacity: 0;
  transition:
    opacity var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.task-msg:hover .task-msg__edit,
.task-msg__edit:focus-visible {
  opacity: 1;
}
.task-msg__edit:hover {
  color: var(--ink);
}
@media (hover: none) {
  .task-msg__edit {
    opacity: 1;
  }
}
.task-msg__edited {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}
.task-msg__error,
.task-chat__error {
  color: var(--danger-ink);
}
.task-msg__error {
  font-size: 12px;
  line-height: var(--lh-12);
}
.task-msg__text {
  color: var(--ink);
  white-space: pre-wrap;
}
.task-markdown {
  white-space: normal;
  overflow-wrap: anywhere;
}
.task-markdown :deep(> :first-child) {
  margin-top: 0;
}
.task-markdown :deep(> :last-child) {
  margin-bottom: 0;
}
.task-markdown :deep(p) {
  margin: 0 0 8px;
}
.task-markdown :deep(ul),
.task-markdown :deep(ol) {
  padding-left: 20px;
  margin: 4px 0 8px;
}
.task-markdown :deep(code) {
  padding: 0.5px 5px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-family: var(--font-mono);
  font-size: 0.88em;
}
.task-markdown :deep(pre) {
  max-width: 100%;
  overflow-x: auto;
  margin: 4px 0 8px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--fill);
}
.task-markdown :deep(pre code) {
  padding: 0;
  border: 0;
  background: none;
}
.task-steps {
  padding: 2px 0;
}
.task-steps__head {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px 2px 2px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.task-steps__head:hover {
  background: var(--fill);
}
.task-steps__failed {
  color: var(--danger-ink);
}
.task-steps__list {
  margin: 2px 0 4px 20px;
  padding: 0;
  list-style: none;
}
.task-step {
  display: flex;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.task-step__verb {
  flex: 0 0 auto;
  color: var(--text);
}
.task-step__arg {
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-step--failed .task-step__verb {
  color: var(--danger-ink);
}
.task-chat__say {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  margin: 8px 16px 12px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.task-chat__say:focus-within {
  border-color: var(--line-2);
}
.task-chat__input {
  flex: 1 1 auto;
  min-width: 0;
  resize: none;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--ink);
}
.task-chat__input::placeholder {
  color: var(--faint);
}
.task-chat__send {
  flex: none;
  padding: 4px 12px;
  border-radius: var(--radius-md);
  color: var(--muted);
  cursor: pointer;
}
.task-chat__send:disabled {
  cursor: default;
  color: var(--faint);
}
.task-chat__send:not(:disabled):hover {
  color: var(--ink);
}
.task-chat__blocked {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 8px 16px 12px;
  padding: 8px 12px;
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-md);
  color: var(--muted);
}
.task-chat__room {
  margin-left: auto;
  padding: 0;
  border: 0;
  background: none;
  color: var(--text);
  cursor: pointer;
}
.task-chat__room:hover {
  color: var(--ink);
  text-decoration: underline;
}
.task-chat__error {
  margin: 0 16px 8px;
}
</style>
