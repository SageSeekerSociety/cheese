<script setup lang="ts">
// The right panel beside 项目文件, for the open file: 历史 (every change that
// took effect on 正式版, the accepted task and each pause in a person's typing
// included, each one undoable), 待定改动 (芝士's pending changes touching this
// file: a 一件事's change waiting for 采纳 or a suggestion in a document; and
// the open branch, if it changed the file) and 对话 (the channel messages that
// mention this file, each linking back to its place in the channel, and a
// 问芝士 box that asks with the file, and the selected lines, as context; the
// question and the answer land in the channel too). In a project claimed from
// the course there is no 芝士 change to wait on: 评测记录 lists every 提交评测,
// each pinned to the version it judged, and opening one shows that version.
import type { PanelTab } from '@/components/panels/PanelTabs.vue'

import { computed } from 'vue'

import { UNITS } from './course'
import { channelOf, isClaim, MINUTES_DOC, NAMES, TASK_DIFFS } from './data'
import DemoComposer from './DemoComposer.vue'
import JudgeResult from './JudgeResult.vue'
import { hhmm, whenText } from './shapes'
import {
  accept,
  askAboutFile,
  branchChanges,
  jumpTo,
  mentionsOf,
  openThing,
  openVersion,
  S,
  snapshotAll,
  suggAccept,
  suggReject,
  t4,
  undo,
} from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import ChangesDiff from '@/components/panels/ChangesDiff.vue'
import DocSuggestionCard from '@/components/panels/doc/DocSuggestionCard.vue'
import PanelTabs from '@/components/panels/PanelTabs.vue'
import { parseDiffLines } from '@/lib/diff'
import { relTime } from '@/lib/relTime'

const research = computed(() => S.project === 'research')
const suggestion = computed(() => (!research.value || S.suggAccepted || S.suggRejected ? 0 : 1))
/** 芝士's task change to this file, while the task waits for 采纳. */
const taskDiff = computed(() =>
  research.value && t4()?.status === 'wait' ? TASK_DIFFS.find((d) => d.path === S.file) ?? null : null
)
const taskLines = computed(() => (taskDiff.value ? parseDiffLines(taskDiff.value.body) : []))
const onBranch = computed(() =>
  S.branch?.project === S.project ? branchChanges().find((c) => c.path === S.file) ?? null : null
)
const pending = computed(() => suggestion.value + (taskDiff.value ? 1 : 0))
const history = computed(() => S.history[S.project])
const subs = computed(() => (isClaim(S.project) ? S.subs[S.project] : []))
const mentions = computed(() => mentionsOf(S.file))
const sel = computed(() => (S.codeSel?.path === S.file ? S.codeSel : null))
const selLabel = computed(() =>
  sel.value ? `带上第 ${sel.value.start}${sel.value.end > sel.value.start ? `–${sel.value.end}` : ''} 行` : null
)

const tabs = computed<PanelTab[]>(() => [
  ...(research.value
    ? []
    : [
        {
          key: 'subs',
          label: '评测记录',
          icon: 'mdi-clipboard-check-outline',
          signal: subs.value.length ? { kind: 'count' as const, count: subs.value.length } : undefined,
        },
      ]),
  { key: 'history', label: '历史', icon: 'mdi-history' },
  ...(research.value
    ? [
        {
          key: 'pending',
          label: '待定改动',
          icon: 'mdi-file-document-edit-outline',
          signal: pending.value ? { kind: 'count' as const, count: pending.value, fresh: true } : undefined,
        },
      ]
    : []),
  {
    key: 'talk',
    label: '对话',
    icon: 'mdi-message-outline',
    signal: mentions.value.length ? { kind: 'count' as const, count: mentions.value.length } : undefined,
  },
])
const tab = computed(() => (S.ftab === 'history' || S.ftab === 'talk' ? S.ftab : research.value ? 'pending' : 'subs'))
/** A message as one plain paragraph: names for mentions, no Markdown marks, code blocks left out. */
const plain = (t: string) =>
  t
    .replace(/```[\s\S]*?```/g, '')
    .replace(/[`*]/g, '')
    .replace(/<@cheese>/g, '@芝士')
    .replace(/<@(\w+)>/g, (_, h) => `@${NAMES[h] ?? h}`)
    .trim()
function undoAt(i: number) {
  // A burst of typing still open becomes its own entry first, so 撤回 takes back what was clicked.
  const n = history.value.length
  snapshotAll()
  undo(i + history.value.length - n)
}
</script>

<template>
  <aside class="ot-fp" data-zone="right">
    <header class="ot-fp__head">
      <span class="ot-fp__name">{{ S.file.split('/').pop() }}</span>
      <span class="ot-fp__dir">{{ S.file.includes('/') ? S.file.slice(0, S.file.lastIndexOf('/')) : '' }}</span>
    </header>
    <PanelTabs :tabs="tabs" :active="tab" @select="S.ftab = $event" />
    <div class="ot-fp__body" :class="{ 'ot-fp__body--talk': tab === 'talk' }">
      <div v-if="tab === 'subs'" class="ot-subs" data-zone="subs">
        <BaseEmptyState
          v-if="!subs.length"
          size="compact"
          icon="mdi-clipboard-check-outline"
          title="还没有提交评测"
          desc="在要评测的代码文件上点「提交评测」，每次评的版本都记在这里"
        />
        <div v-for="x in subs" :key="x.id" class="ot-subs__item" :class="{ 'is-open': S.viewSub === x.id }">
          <button type="button" class="ot-subs__row" :data-sub="x.id" @click="openVersion(x.id)">
            <span class="ot-subs__line"
              >第 {{ x.n }} 次提交 ·
              <b :class="{ 'is-ok': x.score === x.total }">{{ x.score }}/{{ x.total }}</b>
              · 版本 <code>{{ x.version }}</code></span
            >
            <span class="ot-subs__meta"
              >{{ S.project === 'week3' ? `${UNITS[x.unit]!.title} · ` : '' }}{{ whenText(x.at) }}</span
            >
          </button>
          <JudgeResult v-if="S.viewSub === x.id" :sub="x" class="ot-subs__detail" />
        </div>
      </div>
      <template v-else-if="tab === 'pending'">
        <div v-if="onBranch" class="ot-fp__copy">
          <v-icon size="16" icon="mdi-source-branch" />
          <span>分支「{{ S.branch!.name }}」改了这个文件 {{ onBranch.n }} 行</span>
          <span class="ot-fp__copy-note">还没合回</span>
        </div>
        <div v-if="taskDiff" class="ot-fp__card ot-fp__task" data-zone="task-pending">
          <div class="ot-fp__task-head">
            <CheeseAvatar :size="20" name="芝士" handle="cheese" />
            <span class="ot-fp__task-title">任务「{{ t4()!.title }}」改了这个文件，等你采纳</span>
          </div>
          <div class="ot-fp__task-diff"><ChangesDiff :lines="taskLines" inline /></div>
          <div class="ot-fp__task-actions">
            <BaseButton kind="ghost" size="sm" @click="openThing('t4'), (S.rtab = 'changes')">去任务里看</BaseButton>
            <BaseButton kind="primary" size="sm" @click="accept">采纳</BaseButton>
          </div>
        </div>
        <template v-if="suggestion">
          <p v-if="S.file !== MINUTES_DOC" class="ot-fp__other">这个文件之外，芝士还有一条待定的建议：</p>
          <div class="ot-fp__card" data-zone="suggestion">
            <DocSuggestionCard
              author-name="芝士"
              author-handle="cheese"
              author-agent
              author-avatar=""
              :index="0"
              :total="1"
              reason="《组会纪要》第 3 条：「（待补）」改成「下周组会前把 COCO 5K 的结果更新到这里。」"
              editable
              @accept="suggAccept"
              @reject="suggReject"
            />
          </div>
        </template>
        <BaseEmptyState v-else-if="!taskDiff && !onBranch" size="compact" icon="mdi-check-all" title="暂无待定的改动" />
      </template>
      <template v-else-if="tab === 'talk'">
        <div class="ot-talk" data-zone="file-talk">
          <p v-if="!mentions.length" class="ot-fp__other">频道里还没人提到这个文件。在下面问芝士，问题会发到频道里。</p>
          <div v-for="m in mentions" :key="m.line.id" class="ot-talk__item">
            <CheeseAvatar v-if="m.line.who === 'cheese'" :size="22" name="芝士" handle="cheese" />
            <UserAvatar v-else :size="22" :name="NAMES[m.line.who]" :seed="m.line.who" />
            <div class="ot-talk__main">
              <div class="ot-talk__meta">
                <b>{{ NAMES[m.line.who] }}</b>
                <span>{{ hhmm(m.line.at) }}</span>
                <span class="ot-head__gap" />
                <button
                  type="button"
                  class="ot-talk__jump"
                  :data-jump="m.line.id"
                  title="在频道里看这条消息"
                  @click="jumpTo(m.chan, m.line.id)"
                >
                  # {{ channelOf(m.chan)?.title }}<v-icon size="13" icon="mdi-arrow-top-right" />
                </button>
              </div>
              <div class="ot-talk__text">{{ plain(m.line.text) }}</div>
            </div>
          </div>
        </div>
      </template>
      <ol v-else class="ot-hist">
        <li v-for="(h, i) in history" :key="h.what + h.at" class="ot-hist__row" :class="{ 'ot-flash': h.fresh }">
          <CheeseAvatar v-if="h.who === 'cheese'" :size="22" name="芝士" handle="cheese" />
          <UserAvatar v-else :size="22" :name="NAMES[h.who]" :seed="h.who" />
          <div class="ot-hist__text">
            <span
              ><b>{{ NAMES[h.who] }}</b> {{ h.what }}</span
            >
            <span class="t-meta-read" :title="new Date(h.at).toLocaleString()">{{ relTime(h.at) }}</span>
          </div>
          <BaseButton v-if="!h.fixed" class="ot-hist__undo" kind="ghost" size="sm" @click="undoAt(i)">撤回</BaseButton>
        </li>
      </ol>
    </div>
    <DemoComposer
      v-if="tab === 'talk'"
      v-model="S.asking"
      :placeholder="research ? '问芝士，带着这个文件' : '问芝士思路（它只给提示）'"
      :reply-label="selLabel"
      @clear-reply="S.codeSel = null"
      @send="askAboutFile"
    />
  </aside>
</template>

<style scoped>
.ot-subs__item {
  border-bottom: 1px solid var(--line);
}

.ot-subs__row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 8px;
  font: inherit;
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
}

.ot-subs__row:hover {
  background: var(--fill);
}

.ot-subs__item.is-open .ot-subs__row {
  background: var(--line-2);
}

.ot-subs__line {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.ot-subs__line b {
  font-family: var(--font-mono);
  color: var(--ink);
}

.ot-subs__line b.is-ok {
  color: var(--ok-ink);
}

.ot-subs__line code {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink);
}

.ot-subs__meta {
  font-size: 12px;
  color: var(--muted);
}

.ot-subs__detail {
  padding: 4px 8px 12px;
}

.ot-fp {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.ot-fp__head {
  display: flex;
  align-items: center;
  height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid var(--line);
}

.ot-fp__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.ot-fp__dir {
  margin-left: 8px;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-fp__copy {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--ink);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ot-fp__copy .v-icon {
  flex: none;
  color: var(--muted);
}

.ot-fp__copy-note {
  margin-left: auto;
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}

.ot-fp__other {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--muted);
}

.ot-fp__body--talk {
  padding: 8px 12px;
}

.ot-fp__task {
  margin-bottom: 12px;
  overflow: hidden;
}

.ot-fp__task-head {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--ink);
}

.ot-fp__task-diff {
  max-height: 220px;
  overflow: auto;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.ot-fp__task-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  padding: 8px;
}

.ot-talk {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ot-talk__item {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 8px 4px;
  border-bottom: 1px solid var(--line);
}

.ot-talk__main {
  flex: 1;
  min-width: 0;
}

.ot-talk__meta {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 12px;
  color: var(--muted);
}

.ot-talk__meta b {
  font-size: 13px;
  color: var(--ink);
}

.ot-talk__jump {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  padding: 0 4px;
  font: inherit;
  color: var(--muted);
  white-space: nowrap;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.ot-talk__jump:hover {
  color: var(--ink);
  background: var(--fill);
}

.ot-talk__text {
  display: -webkit-box;
  margin-top: 2px;
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  white-space: pre-wrap;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
}

.ot-hist__row:hover {
  background: var(--fill);
}

.ot-hist__undo {
  opacity: 0;
}

.ot-hist__row:hover .ot-hist__undo,
.ot-hist__undo:focus-visible {
  opacity: 1;
}

.ot-fp__body {
  flex: 1;
  min-height: 0;
  padding: 16px;
  overflow: auto;
}

.ot-fp__card {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ot-fp__hint {
  margin: 12px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.ot-hist {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.ot-hist__row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 8px;
  border-radius: var(--radius-md);
}

.ot-hist__text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.ot-hist__text b {
  color: var(--ink);
}
</style>
