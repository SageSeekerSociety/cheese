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
      <span class="ap__who">
        <span class="ap__name">{{ t('work.agentAvatar.defaultName') }}</span>
      </span>
      <span class="ap__grow" />
      <BaseButton
        icon="mdi-pencil-outline"
        size="sm"
        :disabled="busy || (messages.length === 0 && streaming === null)"
        :title="t('tasks.assistant.newTopic')"
        :aria-label="t('tasks.assistant.newTopic')"
        @click="!busy && emit('new')"
      />
      <BaseButton
        v-if="dockable"
        :icon="docked ? 'mdi-dock-window' : 'mdi-dock-right'"
        size="sm"
        :title="docked ? t('tasks.assistant.undock') : t('tasks.assistant.dock')"
        :aria-label="docked ? t('tasks.assistant.undock') : t('tasks.assistant.dock')"
        @click="emit('dock', !docked)"
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

    <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- only catches clicks on the links in the answers; Enter on a focused link fires the same click -->
    <div ref="scroller" class="ap__chat" @click="followLink" @scroll.passive="nearTop">
      <button v-if="hasOlder" type="button" class="ap__older" @click="readBack(() => emit('older'))">
        {{ t('tasks.assistant.olderTopics') }}
      </button>
      <section v-for="topic in earlier" :key="topic.id" class="ap__topic" :aria-label="topic.title">
        <button
          v-if="topic.earlier"
          type="button"
          class="ap__older"
          @click="readBack(() => emit('olderMessages', topic.id))"
        >
          {{ t('tasks.assistant.olderMessages') }}
        </button>
        <template v-for="(m, i) in topic.messages" :key="i">
          <AssistantAsked
            v-if="m.role === 'user'"
            :text="m.text"
            :files="m.files"
            :asked-on="m.askedOn"
            :selection="m.selection"
            :cited="m.cited"
          />
          <p v-else-if="m.role === 'platform'" class="ap__run">{{ runLine(m.shown) }}</p>
          <div v-else class="ap__a">
            <span class="ap__avatar" aria-hidden="true"><CheeseAvatar :size="26" /></span>
            <AssistantTurn
              class="ap__text"
              :pieces="interleave(m.text, m.steps ?? [])"
              :checklist="checklistOf(m.steps ?? [])"
              :changes="m.changes ?? []"
              :results="m.results ?? []"
              :proposals="m.proposals ?? []"
              :files="m.handed ?? []"
              :rules="m.rules ?? []"
              @download-file="(file) => emit('downloadFile', file)"
            >
              <template #text="{ text }">
                <!-- eslint-disable-next-line vue/no-v-html -- sanitized by MarkdownRenderer (DOMPurify) -->
                <div class="ap__md" v-html="render(text)" />
              </template>
              <p v-if="m.stopped" class="ap__stopped">{{ t('tasks.assistant.stopped') }}</p>
              <p v-else-if="m.silent && !m.text" class="ap__stopped">{{ t('tasks.assistant.silent') }}</p>
            </AssistantTurn>
          </div>
        </template>
        <p class="ap__divider">
          <span>{{ t('tasks.assistant.newTopic') }}</span>
        </p>
      </section>
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

      <button
        v-if="currentEarlier"
        type="button"
        class="ap__older"
        @click="readBack(() => emit('olderMessages', null))"
      >
        {{ t('tasks.assistant.olderMessages') }}
      </button>
      <template v-for="(m, i) in messages" :key="i">
        <p v-if="dayOf(i)" class="ap__day">{{ dayOf(i) }}</p>
        <AssistantAsked
          v-if="m.role === 'user'"
          :text="m.text"
          :files="m.files"
          :asked-on="m.askedOn"
          :selection="m.selection"
          :cited="m.cited"
        />
        <p v-else-if="m.role === 'platform'" class="ap__run">{{ runLine(m.shown) }}</p>
        <div v-else class="ap__a">
          <span class="ap__avatar" aria-hidden="true"><CheeseAvatar :size="26" /></span>
          <AssistantTurn
            class="ap__text"
            :pieces="interleave(m.text, m.steps ?? [])"
            :checklist="checklistOf(m.steps ?? [])"
            :changes="m.changes ?? []"
            :results="m.results ?? []"
            :proposals="m.proposals ?? []"
            :files="m.handed ?? []"
            :rules="m.rules ?? []"
            :sending="sendingProposal"
            @send-proposal="(id, edited) => emit('sendProposal', id, edited)"
            @dismiss-proposal="(id) => emit('dismissProposal', id)"
            @open-place="(task) => emit('openPlace', task)"
            @undo="(id, key) => emit('undo', id, key)"
            @undo-all="(rows) => emit('undoAll', rows)"
            @jump="(key) => emit('jump', key)"
            @open-draft="(id) => emit('openDraft', id)"
            @open-task="(id) => emit('openTask', id)"
            @download-file="(file) => emit('downloadFile', file)"
          >
            <template #text="{ text }">
              <!-- eslint-disable-next-line vue/no-v-html -- sanitized by MarkdownRenderer (DOMPurify) -->
              <div class="ap__md" v-html="render(text)" />
            </template>
            <p v-if="m.stopped" class="ap__stopped">{{ t('tasks.assistant.stopped') }}</p>
            <p v-else-if="m.silent && !m.text" class="ap__stopped">{{ t('tasks.assistant.silent') }}</p>
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
          :pieces="livePieces"
          :checklist="checklistOf(steps)"
          :changes="changes"
          :results="results"
          :proposals="proposals"
          :files="handed"
          :rules="rules"
          :sending="sendingProposal"
          @send-proposal="(id, edited) => emit('sendProposal', id, edited)"
          @dismiss-proposal="(id) => emit('dismissProposal', id)"
          @open-place="(task) => emit('openPlace', task)"
          @undo="(id, key) => emit('undo', id, key)"
          @undo-all="(rows) => emit('undoAll', rows)"
          @jump="(key) => emit('jump', key)"
          @open-draft="(id) => emit('openDraft', id)"
          @open-task="(id) => emit('openTask', id)"
          @download-file="(file) => emit('downloadFile', file)"
        >
          <template #text="{ text }">
            <!-- eslint-disable-next-line vue/no-v-html -- sanitized by MarkdownRenderer (DOMPurify) -->
            <div class="ap__md" v-html="render(text)" />
          </template>
          <p v-if="!streaming && queued" class="ap__looking">{{ t('tasks.assistant.queued') }}</p>
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
      <Transition name="ap-strip">
        <div v-if="voice.trouble.value" class="ap__strip" role="status">
          <v-icon size="16">{{
            voice.trouble.value.kind === 'denied' ? 'mdi-microphone-off' : 'mdi-alert-circle-outline'
          }}</v-icon>
          <span class="ap__strip-text">
            {{
              voice.trouble.value.kind === 'denied'
                ? t('work.room.composer.voice.denied')
                : voice.trouble.value.message || t('work.room.composer.voice.failed')
            }}
          </span>
          <button
            type="button"
            class="ap__strip-x"
            :aria-label="t('work.room.composer.voice.dismiss')"
            :title="t('work.room.composer.voice.dismiss')"
            @click="voice.trouble.value = null"
          >
            <v-icon size="14">mdi-close</v-icon>
          </button>
        </div>
      </Transition>
      <!-- The same box as the channel composer's: the chip row, the editor, the action row. -->
      <div class="ap__box">
        <CiteMenu
          v-if="cite.open.value"
          ref="citeMenu"
          class="ap__cite-menu"
          :picker="cite"
          :project="project"
          :most="MOST_CITED"
        />
        <AssistantContextRow
          :page="pageLabel"
          :page-off="pageOff"
          :selection="selection"
          :atts="chips"
          @toggle-page="emit('togglePage')"
          @clear-selection="emit('clearSelection')"
          @remove-att="(i: number) => emit('removeFile', i)"
        />
        <ComposerEditor
          ref="editor"
          v-model="draft"
          v-model:caret="caret"
          class="ap__input"
          :names="names"
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
          :no-files="!canAttach"
          :files-label="t('tasks.assistant.files.add')"
          :busy="busy && !draft.trim() && !files.length"
          :can-voice="canVoice"
          :voice="voice.state.value"
          :voice-swap="voiceSwap"
          @files="emit('attach', $event)"
          @send="submit"
          @stop="emit('stop')"
          @voice="onVoice"
          @voice-done="voice.finish()"
          @voice-swap="swapVoice"
        >
          <template #chips>
            <BaseButton
              kind="ghost"
              size="sm"
              icon="mdi-at"
              class="ap__cite"
              :aria-label="t('tasks.assistant.cite.button')"
              :title="t('tasks.assistant.cite.button')"
              @mousedown.prevent
              @click="cite.start(), editor?.focus?.()"
            />
          </template>
          <!-- The recording bar grows out of the microphone, as in the channel composer. -->
          <template #voice>
            <Transition name="voice-grow" :duration="{ enter: 320, leave: 180 }">
              <ComposerVoiceBar
                v-if="voiceOpen"
                :state="voice.state.value"
                :elapsed="voice.elapsed.value"
                :levels="voice.levels.value"
                :max-seconds="VOICE_MAX_SECONDS"
                :holding="holding"
                :drag-x="dragX"
                :armed="armed"
                @cancel="voice.cancel()"
              />
            </Transition>
          </template>
        </ComposerActions>
      </div>
      <p class="ap__hint">{{ hint ?? t('tasks.assistant.hint') }}</p>
    </footer>
  </section>
</template>

<script setup lang="ts">
// 个人芝士在一个地方的面板（#2285）：这里的几段对话、当前这段的内容、输入框。题目页、
// 发布页、改题页用的都是它；芝士每一轮做的事（调的工具、写进草稿的改动、发布的结果）
// 由 `AssistantTurn` 画。只拿 props、只发事件：说话、换对话、撤销、交文件都由外面那一层去做。
import type { VoiceHeard } from '@/composables/useVoiceInput'
import type { PanelChange, PanelProposal, PanelResult, PanelStep, TurnPiece } from '@/lib/assistantView'
import type { PendingAttachment } from '@/lib/attachments'
import type { RefNames } from '@/lib/refChip'
import type { AssistantCited, AssistantHanded, AssistantRule, AssistantShown } from '@/stores/agentThread'
import type { Citable } from '@/types/citable'

import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'
import dayjs from 'dayjs'

import { MOST_CITED, useCitePicker } from '@/composables/useCitePicker'
import { useComposerVoice } from '@/composables/useComposerVoice'

import AssistantAsked from './AssistantAsked.vue'
import AssistantContextRow from './AssistantContextRow.vue'
import AssistantTurn from './AssistantTurn.vue'
import CiteMenu from './CiteMenu.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { MarkdownRenderer } from '@/components/chat/services/markdownRenderer'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import NavLink from '@/components/common/NavLink.vue'
import ComposerActions from '@/components/room/ComposerActions.vue'
import ComposerEditor from '@/components/room/ComposerEditor.vue'
import ComposerVoiceBar from '@/components/room/ComposerVoiceBar.vue'
import { runLine } from '@/lib/agentRules'
import { checklistOf, interleave } from '@/lib/assistantView'

/** 之前的一个话题：读回来只给看。 */
export interface PanelTopic {
  id: string
  title: string
  at: string
  messages: PanelMessage[]
  /** 还有更早的消息没读回来。 */
  earlier?: boolean
}

export interface PanelMessage {
  role: 'user' | 'assistant' | 'platform'
  text: string
  /** 平台开始一次运行的那一句：哪条自动化、发生了什么。 */
  shown?: AssistantShown
  /** 回答被停下了，`text` 是停下时写出的部分。 */
  stopped?: boolean
  at: string
  /** 随问题交给芝士的文件名。 */
  files?: string[]
  /** 人问的：在哪一页问的、带着选中的哪段字、引用了什么。 */
  askedOn?: string
  selection?: string
  cited?: AssistantCited[]
  steps?: PanelStep[]
  changes?: PanelChange[]
  results?: PanelResult[]
  proposals?: PanelProposal[]
  /** 芝士交给人的文件、建或改的自动化。 */
  handed?: AssistantHanded[]
  rules?: AssistantRule[]
  /** 做了事，却一个字没写。 */
  silent?: boolean
}

/** 要随下一个问题交给芝士的文件。 */
export interface PanelFile {
  name: string
  mime?: string
  uploading?: boolean
}

const props = withDefaults(
  defineProps<{
    messages: PanelMessage[]
    /** 当前话题上面摆着的之前几个话题，从早到晚；只给看。 */
    earlier?: PanelTopic[]
    /** 还有更早的话题没摆出来。 */
    hasOlder?: boolean
    /** 当前话题还有更早的消息没读回来。 */
    currentEarlier?: boolean
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
    /** 这一轮替人起草、等人确认的消息和任务。 */
    proposals?: PanelProposal[]
    /** 这一轮交给人的文件。 */
    handed?: AssistantHanded[]
    /** 正在发出的那一件。 */
    sendingProposal?: number | null
    /** 空对话上面那一行（芝士读了什么）；空串就不显示。 */
    context?: string
    greeting?: string
    starters?: string[]
    placeholder?: string
    hint?: string
    /** 能把文件交给芝士。 */
    canAttach?: boolean
    /** 把说的一段交给后端听写；不给就没有麦克风。 */
    hearVoice?: (wav: Blob) => Promise<VoiceHeard>
    files?: PanelFile[]
    closable?: boolean
    /** 桌面上能停在右边：头上给一颗「固定到右侧」或「取消固定」。 */
    dockable?: boolean
    docked?: boolean
    /** 输入框上方那一排：这一页叫什么（没有就不画）、人关掉了没有，和选中交给芝士的那段字。 */
    pageLabel?: string | null
    pageOff?: boolean
    selection?: string | null
    /** `@` 能引用的东西（还没读回来是 null）、最近去过的 id、眼前这个项目的名字。 */
    citable?: Citable[] | null
    recents?: string[]
    project?: string | null
    /** 用 @ 挑过的东西，输入框里的标签照它写名字。 */
    cited?: NonNullable<RefNames['cited']>
    searchProblems?: (query: string) => Promise<Citable[]>
    rules?: AssistantRule[]
  }>(),
  {
    earlier: () => [],
    hasOlder: false,
    currentEarlier: false,
    dockable: false,
    docked: false,
    queued: false,
    creditRefused: false,
    personalTeamHandle: undefined,
    steps: () => [],
    changes: () => [],
    results: () => [],
    proposals: () => [],
    handed: () => [],
    sendingProposal: null,
    context: undefined,
    greeting: undefined,
    starters: undefined,
    placeholder: undefined,
    hint: undefined,
    canAttach: false,
    hearVoice: undefined,
    files: () => [],
    closable: true,
    pageLabel: null,
    pageOff: false,
    selection: null,
    citable: null,
    recents: () => [],
    project: null,
    cited: () => ({}),
    searchProblems: undefined,
    rules: () => [],
  }
)

const emit = defineEmits<{
  send: [text: string]
  new: []
  older: []
  /** 读一个话题（`null` 是当前这个）更早的那一页消息。 */
  olderMessages: [topic: string | null]
  dock: [docked: boolean]
  stop: []
  close: []
  undo: [changeId: number, key: string]
  sendProposal: [id: number, edited: { title?: string; text: string }]
  dismissProposal: [id: number]
  openPlace: [task: string]
  undoAll: [rows: { change: number; key: string }[]]
  jump: [key: string]
  openDraft: [draftId: number]
  openTask: [taskId: number]
  attach: [files: File[]]
  removeFile: [index: number]
  downloadFile: [file: AssistantHanded]
  /** 芝士回答里的站内链接（`/projects/ml-course/channels/3`）：在这个页面里打开。 */
  openLink: [path: string]
  togglePage: []
  clearSelection: []
  /** 打开了 @ 菜单：去读能引用的东西。 */
  citeOpen: []
  cite: [item: Citable]
}>()

/** 回答里指向站内页面的链接不整页刷新，交给外面换页；外链和带修饰键的点击照浏览器的来。 */
function followLink(event: MouseEvent) {
  if (event.defaultPrevented || event.button !== 0) return
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  const link = (event.target as Element | null)?.closest?.('.ap__md a[href]')
  const href = link?.getAttribute('href') ?? ''
  if (!href.startsWith('/') || href.startsWith('//')) return
  event.preventDefault()
  emit('openLink', href)
}

const { t } = useI18n()

const markdown = new MarkdownRenderer()

/** 正在答的这一轮，按发生的顺序。最后做的几步都已回来、还没开始写下一段话时，末尾是
 *  一行「思考中」：芝士在想下一步，不让最后一个工具一直转着。 */
const livePieces = computed<TurnPiece[]>(() => {
  const pieces = interleave(props.streaming ?? '', props.steps)
  const last = pieces[pieces.length - 1]
  if (last?.kind === 'steps' && !props.queued && !props.steps.some((step) => step.state === 'run'))
    last.steps.push({
      key: 'pending',
      verb: t('tasks.assistant.steps.thinking'),
      arg: '',
      state: 'run',
      thinking: true,
    })
  return pieces
})
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
  if (!props.canAttach || !e.dataTransfer?.types.includes('Files')) return
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
    // 芝士还在答时也能说：字和附件都插进这一轮。
    (draft.value.trim().length > 0 || props.files.length > 0) && !props.files.some((f) => f.uploading)
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

function submit() {
  if (!ready.value) return
  // 只附文件不打字也发：文件在这一页是什么意思，页面会告诉芝士。
  emit('send', draft.value.trim())
  draft.value = ''
}

// 说一段：和频道的输入框同一套（`useComposerVoice`）。这里没有名册，说出来的 @ 原样落下。
const editor = ref<InstanceType<typeof ComposerEditor> | null>(null)
const caret = ref(0)
const {
  VOICE_MAX_SECONDS,
  canVoice,
  voice,
  voiceOpen,
  voiceSwap,
  swapVoice,
  onVoice,
  onVoiceKey,
  holding,
  dragX,
  armed,
} = useComposerVoice({
  draft,
  caret,
  editor,
  enabled: () => !!props.hearVoice,
  hear: (wav) => props.hearVoice!(wav),
  expand: (text) => text,
  isImeKey: (e) => composing.value || e.isComposing || e.keyCode === 229,
})

const citeMenu = ref<{ scrollActiveIntoView: () => void } | null>(null)
const cite = useCitePicker({
  draft,
  caret,
  items: () => props.citable,
  recents: () => props.recents,
  project: () => props.project,
  most: MOST_CITED,
  opened: () => emit('citeOpen'),
  picked: (item) => emit('cite', item),
  searchProblems: props.searchProblems,
  scrollActiveIntoView: () => citeMenu.value?.scrollActiveIntoView(),
})
const names = computed<RefNames>(() => ({ ...NO_NAMES, cited: { ...props.cited, ...cite.picks.value } }))

function onKeydown(e: KeyboardEvent) {
  if (cite.onKeydown(e)) return
  if (onVoiceKey(e)) return
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !composing.value) {
    e.preventDefault()
    submit()
  }
}

// 新的话、新的一步出现时贴着底；往上读回来的东西接在上面，不动它。
watch(
  () => [props.messages.at(-1), props.streaming, props.notice, props.steps.length, props.changes.length],
  () => nextTick(() => scroller.value?.scrollTo({ top: scroller.value.scrollHeight })),
  { immediate: true }
)

/** 往上读回来时离底部有多远：读回来的接在上面以后，眼前还是原来那几句。 */
let fromBottom: number | null = null

function readBack(ask: () => void) {
  const el = scroller.value
  fromBottom = el ? el.scrollHeight - el.scrollTop : null
  ask()
}

watch(
  () => [props.earlier, props.messages[0]],
  () =>
    nextTick(() => {
      const el = scroller.value
      if (el && fromBottom !== null) el.scrollTop = el.scrollHeight - fromBottom
      fromBottom = null
    })
)

/** 翻到顶了：还有更早的就接着读，一次一页。 */
function nearTop() {
  const el = scroller.value
  if (!el || el.scrollTop > 48 || fromBottom !== null) return
  // 最上面那个话题没读完就先读它的，读完了才是上一个话题（`older`）。
  const top = props.earlier[0]
  if (top ? top.earlier || props.hasOlder : props.currentEarlier || props.hasOlder) readBack(() => emit('older'))
}
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

.ap__who {
  display: inline-flex;
  gap: 6px;
  align-items: baseline;
  min-width: 0;
  padding: 0 6px;
}

.ap__name {
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
  white-space: nowrap;
}

.ap__older {
  align-self: center;
  padding: 2px 10px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  border-radius: var(--radius-pill);
}

.ap__older:hover {
  color: var(--ink);
  background: var(--fill);
}

.ap__topic {
  display: contents;
}

.ap__divider {
  display: flex;
  gap: 10px;
  align-items: center;
  margin: 4px 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ap__divider::before,
.ap__divider::after {
  flex: 1;
  border-top: 1px solid var(--line-2);
  content: '';
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

/* What started a run on its own: not the person's words, so not a bubble on their side. */
.ap__run {
  align-self: stretch;
  padding: 6px 10px;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  text-align: center;
  background: var(--fill);
  border-radius: var(--radius-sm);
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
  position: relative;
  padding: 4px 6px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  transition:
    border-color var(--dur-quick) var(--ease-standard),
    box-shadow var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard);
}

.ap__cite-menu {
  position: absolute;
  right: 0;
  bottom: calc(100% + 4px);
  left: 0;
  z-index: 3;
}

.ap__box:focus-within {
  border-color: var(--faint);
}

/* 语音没成：贴在输入框顶上的一条，和工作台输入框的提示条同一个样子。 */
.ap__strip {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 8px;
  padding: 4px 4px 4px 12px;
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  background: var(--danger-wash);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--danger-ink);
}
.ap__strip-text {
  flex: 1 1 auto;
  min-width: 0;
  padding: 4px 0;
}
.ap__strip-x {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  background: none;
  border-radius: var(--radius-sm);
  color: inherit;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.ap__strip-x:hover {
  background: var(--fill-2);
}
.ap-strip-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.ap-strip-leave-active {
  transition:
    opacity var(--dur-quick) var(--ease-in),
    transform var(--dur-quick) var(--ease-in);
}
.ap-strip-enter-from,
.ap-strip-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
/* 录音条从麦克风的位置往左展开成整行，说完了收回麦克风（同工作台输入框）。 */
.voice-grow-enter-active :deep(.voice-bar__body) {
  transition:
    clip-path var(--dur-slow) var(--ease-out),
    opacity 120ms var(--ease-out);
}
.voice-grow-leave-active :deep(.voice-bar__body) {
  transition:
    clip-path 180ms var(--ease-in),
    opacity 180ms var(--ease-in);
}
.voice-grow-enter-from :deep(.voice-bar__body),
.voice-grow-leave-to :deep(.voice-bar__body) {
  clip-path: inset(0 0 0 calc(100% - 28px) round var(--radius-md));
  opacity: 0;
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
