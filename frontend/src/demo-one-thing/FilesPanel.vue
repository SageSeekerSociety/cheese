<script setup lang="ts">
// The right panel beside 项目文件, for the open file. It is a place to read
// what happened, not a second place to change things: 采纳 happens only in a
// 一件事. On top, while there is any, 「正在改这个文件的事」: each 一件事 whose
// copy touches this file, with who does it and where it stands; a row opens
// the 一件事 in its channel, on 改动. Under it, 历史: every change that took
// effect on 正式版 (each pause in a person's typing, each 采纳) with 撤回 (the
// newest shows it without a hover), and, under 方案 T1, each 运行 in the
// project's environment, collapsed to one quiet line with no 撤回. No
// submission record either: submissions belong to the 题目 and are listed on it
// in the space.
import { computed } from 'vue'

import { NAMES } from './data'
import { openThing, S, snapshotAll, type Thing, thingsOnFile, undo } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { relTime } from '@/lib/relTime'

const history = computed(() => S.history[S.project])
const onFile = computed(() => thingsOnFile(S.project, S.file))
/** Who does a 一件事 once started: 芝士 unless a person opened it to do it themselves. */
const doer = (t: Thing) => t.doer ?? 'cheese'
const where = (t: Thing) =>
  t.status === 'wait' ? '等采纳' : doer(t) === 'cheese' ? '芝士在做' : `${NAMES[doer(t)]}在改`
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
    <div class="ot-fp__body">
      <section v-if="onFile.length" class="ot-fp__things" data-zone="things-on-file">
        <h3 class="t-eyebrow-read ot-fp__label">正在改这个文件的事</h3>
        <button
          v-for="t in onFile"
          :key="t.id"
          type="button"
          class="ot-fp__thing"
          :data-thing="t.id"
          @click="openThing(t.id, 'changes')"
        >
          <CheeseAvatar v-if="doer(t) === 'cheese'" :size="20" name="芝士" handle="cheese" />
          <UserAvatar v-else :size="20" :name="NAMES[doer(t)]" :seed="doer(t)" />
          <span class="ot-fp__thing-title">{{ t.title }}</span>
          <span class="ot-fp__state" :class="{ 'is-wait': t.status === 'wait' }">{{ where(t) }}</span>
          <v-icon size="14" icon="mdi-chevron-right" class="ot-fp__go" />
        </button>
      </section>
      <h3 class="t-eyebrow-read ot-fp__label">历史</h3>
      <ol class="ot-hist">
        <li
          v-for="(h, i) in history"
          :key="h.what + i"
          class="ot-hist__row"
          :class="{ 'ot-flash': h.fresh, 'ot-hist__row--run': h.run }"
        >
          <template v-if="h.run">
            <v-icon size="14" icon="mdi-play" class="ot-hist__runmark" />
            <span class="ot-hist__runtext"
              >{{ NAMES[h.who] }} {{ h.what }}{{ (h.runs ?? 1) > 1 ? ` · ${h.runs} 次` : '' }}</span
            >
            <span class="t-meta-read" :title="new Date(h.at).toLocaleString()">{{ relTime(h.at) }}</span>
          </template>
          <template v-else>
            <CheeseAvatar v-if="h.who === 'cheese'" :size="22" name="芝士" handle="cheese" />
            <UserAvatar v-else :size="22" :name="NAMES[h.who]" :seed="h.who" />
            <div class="ot-hist__text">
              <span
                ><b>{{ NAMES[h.who] }}</b> {{ h.what }}</span
              >
              <span class="t-meta-read" :title="new Date(h.at).toLocaleString()">{{ relTime(h.at) }}</span>
            </div>
            <BaseButton v-if="!h.fixed" class="ot-hist__undo" kind="ghost" size="sm" @click="undoAt(i)"
              >撤回</BaseButton
            >
          </template>
        </li>
      </ol>
    </div>
  </aside>
</template>

<style scoped>
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

.ot-fp__label {
  margin: 0 0 6px;
  padding: 0 8px;
}

.ot-fp__things {
  margin-bottom: 16px;
}

/* A 一件事 changing this file: the channel's 一件事 card in one line; it opens the 一件事. */
.ot-fp__thing {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  min-height: 36px;
  padding: 6px 8px;
  margin-bottom: 4px;
  font: inherit;
  font-size: 13px;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.ot-fp__thing:hover {
  background: var(--fill);
}

.ot-fp__thing-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-fp__state {
  flex: none;
  font-size: 12px;
  color: var(--muted);
}

/* Waiting on a person: the same amber as anything else that does. */
.ot-fp__state.is-wait {
  color: var(--accent-ink);
}

.ot-fp__go {
  flex: none;
  color: var(--faint);
}

/* A 运行: one quiet line, nothing to take back. */
.ot-hist__row.ot-hist__row--run {
  gap: 6px;
  align-items: center;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--muted);
}

.ot-hist__runmark {
  flex: none;
  color: var(--faint);
}

.ot-hist__runtext {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-hist__row:hover {
  background: var(--fill);
}

.ot-hist__undo {
  opacity: 0;
}

.ot-hist__row:hover .ot-hist__undo,
.ot-hist__row.ot-flash .ot-hist__undo,
.ot-hist__undo:focus-visible {
  opacity: 1;
}

.ot-fp__body {
  flex: 1;
  min-height: 0;
  padding: 16px;
  overflow: auto;
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
