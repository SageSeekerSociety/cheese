<script setup lang="ts">
// The right panel beside 项目文件, for the open file. It is a place to read
// what happened, not a second place to change things: 采纳 happens only in a
// 一件事. On top, while there is any, 「正在改这个文件的事」: each 一件事 whose
// 工作台 has this file, with who is doing it and where it stands; a row opens
// the 一件事 on its 工作台. Under it, 历史, of the whole project or of this
// file only: every change that took effect on 正式版, each saying who and
// through what: 「直接改了」 (a document, typed into) or 「采纳了「<事名>」
// （芝士做的 / 林晓做的）」, the name opening that 一件事. Each has 撤回 (the
// newest shows it without a hover). Runs are not here: they stay in the
// 工作台 of the 一件事 they were made in. No submission record either:
// submissions belong to the 题目 and are listed on it in the space.
import type { HistoryEntry } from './state'

import { computed, ref } from 'vue'

import { NAMES } from './data'
import { doingNow, openBench, openThing, S, type Thing, thingsOnFile, undo } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { relTime } from '@/lib/relTime'

/** 历史 of the whole project, or only what touched this file. */
const scope = ref<'all' | 'file'>('all')
const SCOPES = [
  { value: 'all' as const, label: '整个项目' },
  { value: 'file' as const, label: '这个文件' },
]
const touches = (h: HistoryEntry, path: string) => h.about === path || !!h.prevs?.[path]
const history = computed(() =>
  scope.value === 'all' ? S.history[S.project] : S.history[S.project].filter((h) => touches(h, S.file))
)
/** What an 采纳 entry says after the 一件事's name and who did it: 「：改了 …」. */
const rest = (h: HistoryEntry) => h.what.slice(`采纳了「${h.via!.title}」（${h.via!.by}）`.length)
const onFile = computed(() => thingsOnFile(S.project, S.file))
/** Who is doing a 一件事 now (who did it, once it waits for 采纳). */
const doer = (t: Thing) => doingNow(t) ?? t.doer ?? 'cheese'
const where = (t: Thing) =>
  t.status === 'wait' ? '等采纳' : doer(t) === 'cheese' ? '芝士在做' : `${NAMES[doer(t)]}在做`
const undoAt = (h: HistoryEntry) => undo(S.history[S.project].indexOf(h))
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
          @click="openBench(t.id, S.file)"
        >
          <CheeseAvatar v-if="doer(t) === 'cheese'" :size="20" name="芝士" handle="cheese" />
          <UserAvatar v-else :size="20" :name="NAMES[doer(t)]" :seed="doer(t)" />
          <span class="ot-fp__thing-title">{{ t.title }}</span>
          <span class="ot-fp__state" :class="{ 'is-wait': t.status === 'wait' }">{{ where(t) }}</span>
          <v-icon size="14" icon="mdi-chevron-right" class="ot-fp__go" />
        </button>
      </section>
      <div class="ot-fp__histhead">
        <h3 class="t-eyebrow-read ot-fp__label">历史</h3>
        <span class="ot-head__gap" />
        <SegmentedControl v-model="scope" :options="SCOPES" label="历史的范围" size="sm" data-hist-scope />
      </div>
      <ol class="ot-hist">
        <li v-for="(h, i) in history" :key="h.what + i" class="ot-hist__row" :class="{ 'ot-flash': h.fresh }">
          <CheeseAvatar v-if="h.who === 'cheese'" :size="22" name="芝士" handle="cheese" />
          <UserAvatar v-else :size="22" :name="NAMES[h.who]" :seed="h.who" />
          <div class="ot-hist__text">
            <span v-if="h.via" data-via
              ><b>{{ NAMES[h.who] }}</b> 采纳了「<a
                v-if="h.via.thing"
                href="#"
                class="ot-hist__thing"
                title="打开这件事"
                @click.prevent="openThing(h.via.thing, 'changes')"
                >{{ h.via.title }}</a
              ><template v-else>{{ h.via.title }}</template
              >」<span class="ot-hist__by">（{{ h.via.by }}）</span>{{ rest(h) }}</span
            >
            <span v-else
              ><b>{{ NAMES[h.who] }}</b> {{ h.what }}</span
            >
            <span class="t-meta-read" :title="new Date(h.at).toLocaleString()">{{ relTime(h.at) }}</span>
          </div>
          <BaseButton v-if="!h.fixed" class="ot-hist__undo" kind="ghost" size="sm" @click="undoAt(h)">撤回</BaseButton>
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

.ot-fp__histhead {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
}

.ot-fp__histhead .ot-fp__label {
  margin: 0;
}

/* The 一件事's name runs on with the sentence, wrapping like the words around it: a link, not a button. */
.ot-hist__thing {
  color: var(--accent-ink);
  text-decoration: none;
}

.ot-hist__thing:hover {
  text-decoration: underline;
}

.ot-hist__by {
  color: var(--muted);
}

.ot-hist__text b {
  color: var(--ink);
}
</style>
