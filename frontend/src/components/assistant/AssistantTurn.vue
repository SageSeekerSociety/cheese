<template>
  <!-- One answer of 芝士: what it said and the calls it made, in the order they happened (a run of
       calls, one line each); what it wrote into a draft, as a card with an undo per field; what it
       asks the person to confirm; what it published. -->
  <div class="at">
    <section v-if="checklist?.length" class="at__todo" :aria-label="t('tasks.assistant.checklist.label')">
      <button
        v-if="checklistFoldable"
        type="button"
        class="at__fold"
        :aria-expanded="checklistOpen"
        @click="checklistOpen = !checklistOpen"
      >
        <v-icon size="16" class="at__chev" :class="{ 'at__chev--open': checklistOpen }">mdi-chevron-right</v-icon>
        {{ t('tasks.assistant.checklist.folded', { done: checklistDone, total: checklist.length }) }}
      </button>
      <ol v-if="!checklistFoldable || checklistOpen" class="at__todo-list">
        <li v-for="(item, i) in checklist" :key="i" class="at__todo-item" :class="`at__todo-item--${item.status}`">
          <span class="at__todo-mark" role="img" :aria-label="t(CHECKLIST_SAID[item.status])">
            <v-icon v-if="item.status === 'completed'" size="14">mdi-check</v-icon>
            <span v-else-if="item.status === 'in_progress'" class="at__todo-dot" />
            <span v-else class="at__todo-ring" />
          </span>
          <span data-user-content>{{ item.content }}</span>
        </li>
      </ol>
    </section>
    <template v-for="piece in pieces" :key="piece.key">
      <slot v-if="piece.kind === 'text'" name="text" :text="piece.text" />
      <template v-else>
        <button
          v-if="foldable(piece.steps)"
          type="button"
          class="at__fold"
          :aria-expanded="opened.has(piece.key)"
          :data-testid="'turn-steps-fold'"
          @click="toggle(piece.key)"
        >
          <v-icon size="16" class="at__chev" :class="{ 'at__chev--open': opened.has(piece.key) }"
            >mdi-chevron-right</v-icon
          >
          {{ folded(piece.steps) }}
        </button>
        <ul v-if="!foldable(piece.steps) || opened.has(piece.key)" class="at__steps">
          <template v-for="row in stepRows(piece.steps)" :key="row.key">
            <li v-if="row.kind === 'reads'" class="at__step" :class="`at__step--${readsState(row.steps)}`">
              <span class="at__ico" aria-hidden="true">
                <span v-if="readsState(row.steps) === 'run'" class="at__spin" />
                <v-icon v-else size="14">mdi-check</v-icon>
              </span>
              <span class="at__verb">{{ readsLine(row.steps) }}</span>
              <ul class="at__steps at__reads">
                <li v-for="read in row.steps" :key="read.key" class="at__step">
                  <span>{{ read.verb }}</span>
                  <span v-if="read.arg" class="at__arg" data-user-content>{{ read.arg }}</span>
                </li>
              </ul>
            </li>
            <li v-else class="at__step" :class="[`at__step--${row.step.state}`, { 'at__step--act': acts(row.step) }]">
              <span class="at__ico" aria-hidden="true">
                <span v-if="row.step.state === 'run'" class="at__spin" />
                <v-icon v-else-if="row.step.state === 'done'" size="14">mdi-check</v-icon>
                <v-icon v-else size="14">mdi-close</v-icon>
              </span>
              <button
                v-if="row.step.subagent?.steps.length"
                type="button"
                class="at__sub-open"
                :aria-expanded="opened.has(`sub-${row.step.key}`)"
                @click="toggle(`sub-${row.step.key}`)"
              >
                <span class="at__verb">{{ row.step.verb }}</span>
                <span v-if="row.step.arg" class="at__arg" data-user-content>{{ row.step.arg }}</span>
              </button>
              <template v-else>
                <span class="at__verb">{{ row.step.verb }}</span>
                <span v-if="row.step.arg" class="at__arg" data-user-content>{{ row.step.arg }}</span>
              </template>
              <span v-if="row.step.subagent" class="at__sub-state">{{
                t(`tasks.assistant.subagent.${row.step.subagent.status}`)
              }}</span>
              <span v-if="row.step.state === 'failed' && row.step.error" class="at__err">{{ row.step.error }}</span>
              <ul v-if="row.step.subagent && opened.has(`sub-${row.step.key}`)" class="at__steps at__sub-steps">
                <li v-for="done in row.step.subagent.steps" :key="done.key" class="at__step">
                  <span class="at__verb">{{ done.verb }}</span>
                  <span v-if="done.arg" class="at__arg" data-user-content>{{ done.arg }}</span>
                </li>
              </ul>
            </li>
          </template>
        </ul>
      </template>
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

    <AssistantRuleCard v-for="rule in rules" :key="rule.id" :rule="rule" />

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
// 芝士的一轮回答（`AssistantPanel` 里的一段）：说的话和调的工具按发生的顺序排开
// （`interleave`），接连调的几个工具一行一步，答完每一组收成一行「N 步」；写进草稿的改动
// 是一张卡，每格能撤销；留给人确认的地方是另一张卡；发布的结果带一个「查看题目」。默认
// 插槽放话后面的一句（已停止、排队中）；再后面是它交给人的文件（一份一张卡，点了下载），
// 它建的自动化（`AssistantRuleCard`），和它替人起草、等人确认的消息和任务（`AgentProposal`）。
// 只拿 props、只发事件。
import type { ChecklistItem, PanelChange, PanelProposal, PanelResult, PanelStep, TurnPiece } from '@/lib/assistantView'
import type { AssistantHanded, AssistantRule } from '@/stores/agentThread'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AgentProposal from '@/components/agent/AgentProposal.vue'
import AssistantRuleCard from '@/components/assistant/AssistantRuleCard.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { foldedLine, readsLine, stepRows } from '@/lib/assistantView'
import { fmtBytes } from '@/lib/changesTree'
import { leafOf } from '@/lib/libraryFolders'
import { KIND_ICONS, kindOf } from '@/lib/libraryKinds'

const props = withDefaults(
  defineProps<{
    /** 说的话和调的工具，按发生的顺序；话由外面那一层用 `text` 插槽画。 */
    pieces: TurnPiece[]
    /** 这一轮最后写下的进度清单，画在最上面。 */
    checklist?: ChecklistItem[] | null
    changes: PanelChange[]
    results: PanelResult[]
    /** 替人起草、等人确认的消息和任务。 */
    proposals?: PanelProposal[]
    /** 这一轮交给人的文件：存在他的资料库里，点了下载。 */
    files?: AssistantHanded[]
    /** 这一轮建或改的自动化。 */
    rules?: AssistantRule[]
    /** 正在发出的那一件。 */
    sending?: number | null
    /** 这一轮还在答：步骤不收起。 */
    live?: boolean
  }>(),
  { proposals: () => [], files: () => [], rules: () => [], sending: null, live: false, checklist: null }
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
/** 读屏念出每一项做到哪了。 */
const CHECKLIST_SAID = {
  pending: 'tasks.assistant.checklist.pending',
  in_progress: 'tasks.assistant.checklist.inProgress',
  completed: 'tasks.assistant.checklist.completed',
} as const

const checklistOpen = ref(false)
const checklistDone = computed(() => (props.checklist ?? []).filter((item) => item.status === 'completed').length)
/** 答完了、每一项都做完了：清单收成一行，点开再看。 */
const checklistFoldable = computed(
  () => !props.live && !!props.checklist?.length && checklistDone.value === props.checklist.length
)

/** 点开看过的那几组步骤。 */
const opened = ref(new Set<string>())

function toggle(key: string) {
  const next = new Set(opened.value)
  if (!next.delete(key)) next.add(key)
  opened.value = next
}

/** 答完了、这一组做了两步以上：收成一行，点开再看。 */
function foldable(steps: PanelStep[]): boolean {
  return !props.live && steps.length > 1
}

/** 收起的那一行：做了什么，查阅了几项（`foldedLine`）。 */
function folded(steps: PanelStep[]): string {
  return foldedLine(steps)
}

/** 一组查阅还有一项没读完时转圈，都读完了打勾。 */
function readsState(steps: PanelStep[]): 'run' | 'done' {
  return steps.some((step) => step.state === 'run') ? 'run' : 'done'
}

/** 做了事的一步（改了东西、影响到别人）：加重，好和查阅分开。 */
function acts(step: PanelStep): boolean {
  return step.effect === 'changes' || step.effect === 'reaches others'
}

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
.at__sub-open {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
  padding: 0;
  color: inherit;
  text-align: left;
  border-radius: var(--radius-sm);
}

.at__sub-open:hover .at__verb {
  color: var(--ink);
}

.at__sub-state {
  color: var(--muted);
}

/* 分身做过的几步：缩在它那一行下面，左边一条细线连着。 */
.at__steps.at__sub-steps {
  flex-basis: 100%;
  padding-left: 12px;
  margin: 2px 0 2px 6px;
  border-left: 1px solid var(--line);
}

.at__todo {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.at__todo-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.at__todo-item {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.at__todo-item--completed {
  color: var(--muted);
}

.at__todo-item--in_progress {
  font-weight: 500;
  color: var(--ink);
}

.at__todo-mark {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  align-self: center;
  justify-content: center;
  width: 14px;
  height: 14px;
}

.at__todo-item--completed .at__todo-mark {
  color: var(--ok-ink);
}

.at__todo-dot {
  width: 8px;
  height: 8px;
  background: var(--accent);
  border-radius: 50%;
}

.at__todo-ring {
  width: 10px;
  height: 10px;
  border: 1.5px solid var(--faint);
  border-radius: 50%;
}

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

/* 做了事的一步：比查阅重一级。 */
.at__step--act .at__verb {
  font-weight: 600;
  color: var(--ink);
}

/* 一组查阅里的每一项：缩在组那一行下面。 */
.at__steps.at__reads {
  flex-basis: 100%;
  gap: 2px;
  padding-left: 20px;
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
