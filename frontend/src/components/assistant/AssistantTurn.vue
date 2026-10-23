<template>
  <!-- One answer of 芝士: the calls it made, one line each; what it wrote into a draft, as a card
       with an undo per field; what it asks the person to confirm; what it published; then its words. -->
  <div class="at">
    <template v-if="steps.length">
      <button
        v-if="folded"
        type="button"
        class="at__fold"
        :aria-expanded="open"
        :data-testid="'turn-steps-fold'"
        @click="open = !open"
      >
        <v-icon size="16" class="at__chev" :class="{ 'at__chev--open': open }">mdi-chevron-right</v-icon>
        {{ t('tasks.assistant.steps.folded', { first: (calls[0] ?? steps[0]).verb, n: calls.length || steps.length }) }}
      </button>
      <ul v-if="!folded || open" class="at__steps">
        <li v-for="step in steps" :key="step.key" class="at__step" :class="`at__step--${step.state}`">
          <span class="at__ico" aria-hidden="true">
            <span v-if="step.state === 'run'" class="at__spin" />
            <v-icon v-else-if="step.state === 'done'" size="14">mdi-check</v-icon>
            <v-icon v-else size="14">mdi-close</v-icon>
          </span>
          <span class="at__verb">{{ step.verb }}</span>
          <span v-if="step.arg" class="at__arg" data-user-content>{{ step.arg }}</span>
          <span v-if="step.state === 'failed' && step.error" class="at__err">{{ step.error }}</span>
        </li>
      </ul>
    </template>

    <section
      v-for="change in changes"
      :key="change.id"
      class="at__card"
      :class="{ 'at__card--one': change.collapsed }"
      :aria-label="t('tasks.assistant.changes.title')"
    >
      <header class="at__card-h">
        <span class="at__card-t">{{
          change.title || (change.created ? t('tasks.assistant.changes.created') : t('tasks.assistant.changes.title'))
        }}</span>
        <span v-if="change.ordinal" class="at__card-no">{{
          t('tasks.assistant.changes.ordinal', { n: change.ordinal })
        }}</span>
        <button
          v-if="change.created || change.collapsed"
          type="button"
          class="at__link at__card-name"
          data-user-content
          :title="change.name || t('tasks.assistant.changes.untitled')"
          @click="emit('openDraft', change.draft)"
        >
          {{ change.name || t('tasks.assistant.changes.untitled') }}
        </button>
        <span v-if="change.collapsed" class="at__grow" />
        <span v-if="change.collapsed" class="at__card-no">{{
          t('tasks.assistant.changes.count', { n: change.rows.length })
        }}</span>
      </header>
      <ul v-if="!change.collapsed" class="at__rows">
        <li
          v-for="row in change.rows"
          :key="row.key"
          class="at__row"
          :class="{ 'at__row--off': row.undone || row.kept }"
        >
          <button type="button" class="at__jump" @click="emit('jump', row.key)">
            <span class="at__k">{{ row.label }}</span>
            <span class="at__v" :title="row.value" data-user-content>{{ row.value }}</span>
          </button>
          <BaseButton
            v-if="change.canUndo && !row.undone && !row.kept"
            icon="mdi-undo"
            size="sm"
            kind="ghost"
            :title="t('tasks.assistant.changes.undoOne', { field: row.label })"
            :aria-label="t('tasks.assistant.changes.undoOne', { field: row.label })"
            @click="emit('undo', row.change, row.key)"
          />
        </li>
      </ul>
      <footer v-if="change.canUndo && undoable(change)" class="at__card-f">
        <span>{{
          t(change.title ? 'tasks.assistant.changes.changedCount' : 'tasks.assistant.changes.count', {
            n: undoable(change),
          })
        }}</span>
        <span class="at__grow" />
        <BaseButton size="sm" kind="ghost" @click="emit('undoAll', undoRows(change))">{{
          t('tasks.assistant.changes.undoAll')
        }}</BaseButton>
      </footer>
    </section>

    <section
      v-for="note in notes"
      :key="note.id"
      class="at__card at__card--ask"
      :aria-label="t('tasks.assistant.notes.title')"
    >
      <header class="at__card-h">
        <span class="at__card-t">{{ t('tasks.assistant.notes.title') }}</span>
      </header>
      <ul class="at__rows">
        <li v-for="(item, i) in note.items" :key="i" class="at__note">
          <button type="button" class="at__k at__link" @click="emit('jump', item.key)">{{ item.label }}</button>
          <span class="at__note-t" data-user-content>{{ item.text }}</span>
        </li>
      </ul>
    </section>

    <div v-for="result in results" :key="result.taskId" class="at__result">
      <span class="at__ok" aria-hidden="true"><v-icon size="14">mdi-check</v-icon></span>
      <span class="at__result-t">
        <b data-user-content>{{ result.title }}</b>
        <span v-if="result.sub">{{ result.sub }}</span>
      </span>
      <BaseButton size="sm" kind="secondary" @click="emit('openTask', result.taskId)">{{
        t('tasks.assistant.result.open')
      }}</BaseButton>
    </div>

    <slot />

    <div v-for="file in files" :key="file.path" class="at__result at__file">
      <span class="at__file-i" aria-hidden="true"
        ><v-icon size="18">{{ KIND_ICONS[kindOf(file.path)] }}</v-icon></span
      >
      <span class="at__result-t">
        <b data-user-content>{{ leafOf(file.path) }}</b>
        <span>{{ fmtBytes(file.bytes) }}</span>
      </span>
      <BaseButton size="sm" kind="secondary" @click="emit('downloadFile', file)">{{
        t('work.library.download')
      }}</BaseButton>
    </div>

    <AgentProposal
      v-for="proposal in proposals"
      :key="proposal.id"
      :proposal="proposal"
      :busy="sending === proposal.id"
      @send="(id, edited) => emit('sendProposal', id, edited)"
      @dismiss="(id) => emit('dismissProposal', id)"
      @open="(task) => emit('openPlace', task)"
    />
  </div>
</template>

<script setup lang="ts">
// 芝士的一轮回答（`AssistantPanel` 里的一段）：它调的工具一行一步，答完收成一行「N 步」；
// 写进草稿的改动是一张卡，每格能撤销；留给人确认的地方是另一张卡；发布的结果带一个
// 「查看题目」。话本身由外面那一层塞进默认插槽；话后面是它交给人的文件（一份一张卡，点了
// 下载），和它替人起草、等人确认的消息和任务（`AgentProposal`）。只拿 props、只发事件。
import type { PanelChange, PanelProposal, PanelResult, PanelStep } from '@/lib/assistantView'
import type { AssistantHanded } from '@/stores/agentThread'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AgentProposal from '@/components/agent/AgentProposal.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { fmtBytes } from '@/lib/changesTree'
import { leafOf } from '@/lib/libraryFolders'
import { KIND_ICONS, kindOf } from '@/lib/libraryKinds'

const props = withDefaults(
  defineProps<{
    steps: PanelStep[]
    changes: PanelChange[]
    results: PanelResult[]
    /** 替人起草、等人确认的消息和任务。 */
    proposals?: PanelProposal[]
    /** 这一轮交给人的文件：存在他的资料库里，点了下载。 */
    files?: AssistantHanded[]
    /** 正在发出的那一件。 */
    sending?: number | null
    /** 这一轮还在答：步骤不收起。 */
    live?: boolean
  }>(),
  { proposals: () => [], files: () => [], sending: null, live: false }
)

const emit = defineEmits<{
  undo: [changeId: number, key: string]
  undoAll: [rows: { change: number; key: string }[]]
  jump: [key: string]
  openDraft: [draftId: number]
  openTask: [taskId: number]
  sendProposal: [id: number, edited: { title?: string; text: string }]
  dismissProposal: [id: number]
  /** 打开芝士起草、人发出的那个任务。 */
  openPlace: [task: string]
  /** 下载这一轮交出来的一份文件。 */
  downloadFile: [file: AssistantHanded]
}>()

const { t } = useI18n()
const open = ref(false)

/** 工具调用（推理的那几段不算步数）。 */
const calls = computed(() => props.steps.filter((step) => !step.thinking))

/** 答完了、做了两步以上：收成一行，点开再看。 */
const folded = computed(() => !props.live && props.steps.length > 1)

const notes = computed(() =>
  props.changes.filter((change) => change.notes.length).map((change) => ({ id: change.id, items: change.notes }))
)

function undoRows(change: PanelChange) {
  return change.rows.filter((row) => !row.undone && !row.kept).map((row) => ({ change: row.change, key: row.key }))
}

function undoable(change: PanelChange): number {
  return undoRows(change).length
}
</script>

<style scoped>
.at {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.at__fold {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  align-self: flex-start;
  padding: 2px 4px 2px 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  border-radius: var(--radius-sm);
}

.at__fold:hover {
  color: var(--ink);
}

.at__chev {
  transition: transform var(--dur-quick) var(--ease-standard);
}

.at__chev--open {
  transform: rotate(90deg);
}

.at__steps {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.at__step {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.at__ico {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  align-self: center;
}

.at__step--done .at__ico {
  color: var(--ok-ink);
}

.at__step--failed .at__ico,
.at__err {
  color: var(--danger-ink);
}

.at__spin {
  width: 11px;
  height: 11px;
  border: 1.5px solid var(--line-2);
  border-top-color: var(--muted);
  border-radius: 50%;
  animation: at-spin 0.8s linear infinite;
}

@keyframes at-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .at__spin {
    animation: none;
  }
}

.at__verb {
  color: var(--text);
}

.at__arg {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--muted);
}

.at__err {
  flex-basis: 100%;
  padding-left: 20px;
}

.at__card {
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}

.at__card-h {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  border-bottom: 1px solid var(--line);
}

.at__card-t {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--ink);
}

.at__card-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.at__rows {
  display: flex;
  flex-direction: column;
  padding: 4px;
  margin: 0;
  list-style: none;
}

.at__row {
  display: flex;
  gap: 2px;
  align-items: center;
  border-radius: var(--radius-sm);
}

.at__row:hover {
  background: var(--fill);
}

.at__jump {
  display: grid;
  flex: 1;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: 8px;
  align-items: baseline;
  min-width: 0;
  padding: 6px 8px;
  text-align: left;
}

.at__k {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}

.at__v {
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.at__row--off .at__v {
  color: var(--muted);
}

.at__row--off .at__v:not(:only-child) {
  text-decoration: line-through;
}

.at__card-f {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 4px 8px 4px 12px;
  font-size: 13px;
  color: var(--muted);
  border-top: 1px solid var(--line);
}

.at__grow {
  flex: 1;
}

.at__note {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
}

.at__note-t {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.at__link {
  padding: 0;
  color: var(--accent-ink);
  text-align: left;
}

.at__link:hover {
  text-decoration: underline;
}

.at__card--one .at__card-h {
  border-bottom: 0;
}

.at__card--ask .at__card-h {
  background: var(--warn-wash);
}

.at__card-no {
  flex-shrink: 0;
  font-size: 13px;
  color: var(--muted);
  white-space: nowrap;
}

.at__result {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}

.at__ok {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  color: var(--ok-ink);
  background: var(--ok-wash);
  border-radius: var(--radius-pill);
}

.at__file-i {
  display: flex;
  flex-shrink: 0;
  color: var(--muted);
}

.at__result-t {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.at__result-t b {
  overflow: hidden;
  font-weight: 600;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
