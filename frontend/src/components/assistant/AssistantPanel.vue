<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a drop target for files dragged in; the attach button is the keyboard's way -->
  <section
    class="ap"
    :aria-label="t('tasks.assistant.label')"
    @dragenter.prevent="onDragOver"
    @dragover.prevent="onDragOver"
    @dragleave="onDragLeave"
    @drop.prevent="onDrop"
  >
    <header class="ap__head">
      <span class="ap__mark" aria-hidden="true"><CheeseAvatar :size="22" :state="busy ? 'think' : null" /></span>
      <v-menu location="bottom start" :disabled="busy">
        <template #activator="{ props: menu }">
          <button v-bind="menu" type="button" class="ap__title" :disabled="busy">
            <span class="ap__title-text">{{ title || t('tasks.assistant.newConversation') }}</span>
            <v-icon size="16" class="ap__title-caret">mdi-chevron-down</v-icon>
          </button>
        </template>
        <v-list density="compact" class="ap__list" :aria-label="t('tasks.assistant.conversations')">
          <v-list-subheader>{{ t('tasks.assistant.conversations') }}</v-list-subheader>
          <v-list-item
            v-if="currentId === null"
            :active="true"
            :title="t('tasks.assistant.newConversation')"
            :subtitle="t('tasks.assistant.noQuestions')"
          />
          <v-list-item
            v-for="c in conversations"
            :key="c.id"
            :active="c.id === currentId"
            :title="c.title || t('tasks.assistant.newConversation')"
            :subtitle="listSubtitle(c)"
            @click="!busy && emit('select', c.id)"
          />
        </v-list>
      </v-menu>
      <span class="ap__grow" />
      <BaseButton
        icon="mdi-pencil-outline"
        size="sm"
        :disabled="busy"
        :title="t('tasks.assistant.newConversation')"
        :aria-label="t('tasks.assistant.newConversation')"
        @click="!busy && emit('new')"
      />
      <BaseButton
        v-if="closable"
        icon="mdi-close"
        size="sm"
        :title="t('tasks.assistant.close')"
        :aria-label="t('tasks.assistant.close')"
        @click="emit('close')"
      />
    </header>

    <div ref="scroller" class="ap__chat">
      <template v-if="messages.length === 0 && streaming === null">
        <p v-if="context !== ''" class="ap__ctx">{{ context ?? t('tasks.assistant.reads') }}</p>
        <div class="ap__a">
          <span class="ap__avatar" aria-hidden="true"><CheeseAvatar :size="26" /></span>
          <p class="ap__text">{{ greeting ?? t('tasks.assistant.greeting') }}</p>
        </div>
        <div class="ap__starters">
          <button v-for="s in starterList" :key="s" type="button" class="ap__starter" @click="emit('send', s)">
            {{ s }}
          </button>
        </div>
      </template>

      <template v-for="(m, i) in messages" :key="i">
        <p v-if="dayOf(i)" class="ap__day">{{ dayOf(i) }}</p>
        <div v-if="m.role === 'user'" class="ap__q">
          <p class="ap__q-t">{{ m.text }}</p>
          <p v-for="name in m.files ?? []" :key="name" class="ap__q-file">
            <v-icon size="14">mdi-paperclip</v-icon><span data-user-content>{{ name }}</span>
          </p>
        </div>
        <div v-else class="ap__a">
          <span class="ap__avatar" aria-hidden="true"><CheeseAvatar :size="26" /></span>
          <AssistantTurn
            class="ap__text"
            :steps="m.steps ?? []"
            :changes="m.changes ?? []"
            :results="m.results ?? []"
            @undo="(id, key) => emit('undo', id, key)"
            @undo-all="(rows) => emit('undoAll', rows)"
            @jump="(key) => emit('jump', key)"
            @open-draft="(id) => emit('openDraft', id)"
            @open-task="(id) => emit('openTask', id)"
          >
            <!-- eslint-disable-next-line vue/no-v-html -- sanitized by MarkdownRenderer (DOMPurify) -->
            <div v-if="m.text" class="ap__md" v-html="render(m.text)" />
            <p v-if="m.stopped" class="ap__stopped">{{ t('tasks.assistant.stopped') }}</p>
          </AssistantTurn>
        </div>
      </template>

      <div v-if="streaming !== null" class="ap__a">
        <span class="ap__avatar" aria-hidden="true"
          ><CheeseAvatar :size="26" :state="streaming ? null : 'think'"
        /></span>
        <AssistantTurn
          class="ap__text"
          live
          :steps="steps"
          :changes="changes"
          :results="results"
          @undo="(id, key) => emit('undo', id, key)"
          @undo-all="(rows) => emit('undoAll', rows)"
          @jump="(key) => emit('jump', key)"
          @open-draft="(id) => emit('openDraft', id)"
          @open-task="(id) => emit('openTask', id)"
        >
          <p v-if="!streaming && queued" class="ap__looking">{{ t('tasks.assistant.queued') }}</p>
          <!-- eslint-disable-next-line vue/no-v-html -- sanitized by MarkdownRenderer (DOMPurify) -->
          <div v-else-if="streaming" class="ap__md" v-html="render(streaming)" />
        </AssistantTurn>
      </div>

      <p v-if="notice" class="ap__notice" role="status">
        {{ notice }}
        <NavLink
          v-if="creditRefused"
          :to="{ name: 'TeamsDetailCredits', params: { handle: personalTeamHandle } }"
          class="ap__usage"
          >{{ t('usage.viewUsage') }}</NavLink
        >
      </p>
    </div>

    <footer class="ap__foot" :class="{ 'ap__foot--drop': dragOver }">
      <!-- The same box as the channel composer's: the chip row, the editor, the action row. -->
      <div class="ap__box">
        <ComposerChipRow
          v-if="chips.length"
          :atts="chips"
          :topic-id="null"
          @remove-att="(i: number) => emit('removeFile', i)"
        />
        <ComposerEditor
          v-model="draft"
          class="ap__input"
          :names="NO_NAMES"
          :placeholder="placeholder ?? t('tasks.assistant.placeholder')"
          :label="placeholder ?? t('tasks.assistant.placeholder')"
          @keydown="onKeydown"
          @compositionstart="composing = true"
          @compositionend="composing = false"
        />
        <ComposerActions
          :uploading="files.some((f) => f.uploading)"
          :can-send="ready"
          :show-image-picker="canAttach && !mdAndUp"
          :enter-sends="true"
          :no-files="!canAttach || busy"
          :files-label="t('tasks.assistant.files.add')"
          :busy="busy"
          @files="emit('attach', $event)"
          @send="submit"
          @stop="emit('stop')"
        />
      </div>
      <p class="ap__hint">{{ hint ?? t('tasks.assistant.hint') }}</p>
    </footer>
  </section>
</template>

<script setup lang="ts">
// 个人芝士在一个地方的面板（#2285）：这里的几段对话、当前这段的内容、输入框。题目页、
// 发布页、改题页用的都是它；芝士每一轮做的事（调的工具、写进草稿的改动、发布的结果）
// 由 `AssistantTurn` 画。只拿 props、只发事件：说话、换对话、撤销、交文件都由外面那一层去做。
import type { PanelChange, PanelResult, PanelStep } from '@/lib/assistantView'
import type { PendingAttachment } from '@/lib/attachments'
import type { RefNames } from '@/lib/refChip'

import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'
import dayjs from 'dayjs'

import AssistantTurn from './AssistantTurn.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { MarkdownRenderer } from '@/components/chat/services/markdownRenderer'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import NavLink from '@/components/common/NavLink.vue'
import ComposerActions from '@/components/room/ComposerActions.vue'
import ComposerChipRow from '@/components/room/ComposerChipRow.vue'
import ComposerEditor from '@/components/room/ComposerEditor.vue'

export interface PanelConversation {
  id: string
  title: string
  lastActiveAt: string
  questions?: number
}

export interface PanelMessage {
  role: 'user' | 'assistant'
  text: string
  /** 回答被停下了，`text` 是停下时写出的部分。 */
  stopped?: boolean
  at: string
  /** 随问题交给芝士的文件名。 */
  files?: string[]
  steps?: PanelStep[]
  changes?: PanelChange[]
  results?: PanelResult[]
}

/** 要随下一个问题交给芝士的文件。 */
export interface PanelFile {
  name: string
  mime?: string
  uploading?: boolean
}

const props = withDefaults(
  defineProps<{
    conversations: PanelConversation[]
    currentId: string | null
    title: string
    messages: PanelMessage[]
    /** 正在流进来的回答；没在答是 null，刚开始答、还没有字是空串。 */
    streaming: string | null
    /** 芝士此刻在用的工具。 */
    tool: string | null
    /** 在等会话机空出来，还没开始答。 */
    queued?: boolean
    notice: string | null
    /** 被拒是因为额度不够：提示旁给「查看用量」。 */
    creditRefused?: boolean
    personalTeamHandle?: string
    busy: boolean
    /** 这一轮正在做的几步、写的草稿、发布的结果。 */
    steps?: PanelStep[]
    changes?: PanelChange[]
    results?: PanelResult[]
    /** 空对话上面那一行（芝士读了什么）；空串就不显示。 */
    context?: string
    greeting?: string
    starters?: string[]
    placeholder?: string
    hint?: string
    /** 能把文件交给芝士。 */
    canAttach?: boolean
    files?: PanelFile[]
    closable?: boolean
  }>(),
  {
    queued: false,
    creditRefused: false,
    personalTeamHandle: undefined,
    steps: () => [],
    changes: () => [],
    results: () => [],
    context: undefined,
    greeting: undefined,
    starters: undefined,
    placeholder: undefined,
    hint: undefined,
    canAttach: false,
    files: () => [],
    closable: true,
  }
)

const emit = defineEmits<{
  send: [text: string]
  new: []
  select: [id: string]
  stop: []
  close: []
  undo: [changeId: number, key: string]
  undoAll: [rows: { change: number; key: string }[]]
  jump: [key: string]
  openDraft: [draftId: number]
  openTask: [taskId: number]
  attach: [files: File[]]
  removeFile: [index: number]
}>()

const { t } = useI18n()
const markdown = new MarkdownRenderer()
const draft = ref('')
const scroller = ref<HTMLElement | null>(null)
const composing = ref(false)
const { mdAndUp } = useDisplay()

/** 正文里没有 @ 和话题标签可画：编辑器要一份空的名册。 */
const NO_NAMES: RefNames = { mentionNames: {}, topicTitles: {} }

/** 待交给芝士的文件，画成和工作台一样的一排。 */
const chips = computed<PendingAttachment[]>(() =>
  props.files.map((f, i) => ({
    path: `pending-${i}`,
    mime: f.mime || 'application/octet-stream',
    name: f.name,
    uploading: f.uploading,
  }))
)

// 拖文件进面板：整块面板都是落区，亮的是输入框那一圈（和工作台一样）。只认真拖着文件的；
// 拖一段文字经过不亮。
const dragOver = ref(false)
function onDragOver(e: DragEvent) {
  if (!props.canAttach || props.busy || !e.dataTransfer?.types.includes('Files')) return
  dragOver.value = true
}
function onDragLeave(e: DragEvent) {
  const entering = e.relatedTarget
  if (entering instanceof Node && (e.currentTarget as HTMLElement).contains(entering)) return
  dragOver.value = false
}
function onDrop(e: DragEvent) {
  const was = dragOver.value
  dragOver.value = false
  const dropped = Array.from(e.dataTransfer?.files ?? [])
  if (was && dropped.length) emit('attach', dropped)
}

const starterList = computed(
  () => props.starters ?? (['basics', 'start', 'hard'] as const).map((k) => t(`tasks.assistant.starters.${k}`))
)
const ready = computed(
  () =>
    (draft.value.trim().length > 0 || props.files.length > 0) && !props.busy && !props.files.some((f) => f.uploading)
)

function render(text: string): string {
  return markdown.render(text)
}

function day(at: string): string {
  const d = dayjs(at)
  return d.isSame(dayjs(), 'day') ? t('tasks.assistant.today') : d.format(t('tasks.page.dateFormat'))
}

/** 一天里的第一句话上面标一下是哪天。 */
function dayOf(i: number): string {
  const here = day(props.messages[i].at)
  return i === 0 || day(props.messages[i - 1].at) !== here ? here : ''
}

function listSubtitle(c: PanelConversation): string {
  return t('tasks.assistant.listed', { day: day(c.lastActiveAt), n: c.questions ?? 0 })
}

function submit() {
  if (!ready.value) return
  emit('send', draft.value.trim() || t('tasks.assistant.files.readThese'))
  draft.value = ''
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !composing.value) {
    e.preventDefault()
    submit()
  }
}

watch(
  () => [props.messages.length, props.streaming, props.notice, props.steps.length, props.changes.length],
  () => nextTick(() => scroller.value?.scrollTo({ top: scroller.value.scrollHeight })),
  { immediate: true }
)
</script>

<style scoped>
.ap {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--surface);
}

.ap__head {
  display: flex;
  gap: 4px;
  align-items: center;
  height: 48px;
  padding: 0 8px 0 12px;
  border-bottom: 1px solid var(--line);
}

.ap__mark,
.ap__avatar {
  display: inline-flex;
  flex-shrink: 0;
}

.ap__title {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  min-width: 0;
  max-width: 240px;
  padding: 4px 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  border-radius: var(--radius-md);
}

.ap__title:hover:not(:disabled) {
  background: var(--fill);
}

.ap__title-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ap__title-caret {
  color: var(--faint);
}

.ap__grow {
  flex: 1;
}

.ap__chat {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  padding: 16px;
  overflow-y: auto;
}

.ap__ctx {
  padding: 8px 10px;
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-md);
}

.ap__day {
  align-self: center;
  margin: 0;
  font-size: 12px;
  color: var(--faint);
}

.ap__q {
  align-self: flex-end;
  max-width: 85%;
  padding: 8px 12px;
  margin: 0;
  color: var(--ink);
  white-space: pre-wrap;
  background: var(--fill-2);
  border-radius: var(--radius-lg);
}

.ap__q-t {
  margin: 0;
}

.ap__q-file {
  display: flex;
  gap: 4px;
  align-items: center;
  margin: 4px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.ap__a {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.ap__text {
  flex: 1;
  min-width: 0;
  margin: 0;
  color: var(--text);
}

.ap__md :deep(p) {
  margin: 0 0 8px;
}

.ap__md :deep(ul),
.ap__md :deep(ol) {
  padding-left: 18px;
  margin: 0 0 8px;
}

.ap__looking {
  margin: 0;
  color: var(--faint);
}

.ap__stopped {
  margin: 4px 0 0;
  color: var(--faint);
  font-size: 13px;
  line-height: var(--lh-13);
}

.ap__starters {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
  padding-left: 36px;
}

.ap__starter {
  padding: 6px 10px;
  font-size: 13px;
  color: var(--ink);
  text-align: left;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.ap__starter:hover {
  background: var(--fill);
}

.ap__usage {
  margin-left: 8px;
  color: var(--accent-ink);
  font-weight: 600;
  text-decoration: none;
}

.ap__usage:hover {
  text-decoration: underline;
}

.ap__notice {
  padding: 10px 12px;
  margin: 0;
  font-size: 13px;
  color: var(--warn-ink);
  background: var(--warn-wash);
  border-radius: var(--radius-md);
}

.ap__foot {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px calc(14px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
}

.ap__box {
  padding: 4px 6px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  transition:
    border-color var(--dur-quick) var(--ease-standard),
    box-shadow var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard);
}

.ap__box:focus-within {
  border-color: var(--faint);
}

/* 拖着文件进来：和工作台的输入框一样，那一圈加深、垫一层底色。 */
.ap__foot--drop .ap__box {
  border-color: var(--muted);
  box-shadow: inset 0 0 0 1px var(--muted);
  background: var(--fill);
}

.ap__hint {
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}
</style>
