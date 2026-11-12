<script setup lang="ts">
// The right panel beside 项目文件, for the open file. It is a place to read
// what happened, not a second place to change things. On top, while there is
// any, 「在改它的事」: each of 芝士's 一件事 that changes this file, with
// who is doing it and where it stands; a row opens the 一件事 on its 文件.
// Under it two pages. 历史, of the whole project or of this file only: every
// change that took effect, each saying who and how: 「改了 …」 (a person typing
// into the file, one entry per saved stretch) or 「采纳了「<事名>」（芝士做的 /
// 芝士做的，林晓接手改过）」, the name opening that 一件事. Each has 撤回 (the
// newest shows it without a hover). 运行: this file's run records, newest
// first: who ran it, when, which version, the command and what it printed,
// each with 发到频道. Runs never enter 历史. No submission record either:
// submissions belong to the 题目 and are listed on it in the space.
import type { HistoryEntry } from './state'

import { computed, ref } from 'vue'

import { NAMES } from './data'
import { hhmm } from './shapes'
import { openThing, runnable, runsOf, S, shareRun, type Thing, thingsOnFile, undo } from './state'

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
/** Who is doing a 一件事 now: 芝士, or whoever took it over. */
const doer = (t: Thing) => t.takenBy ?? 'cheese'
const where = (t: Thing) =>
  t.status === 'wait' ? '等采纳' : doer(t) === 'cheese' ? '芝士在做' : `${NAMES[doer(t)]}接手在改`

/** 历史 or 运行; 运行 only on a file that runs. */
const PAGES = [
  { value: 'history' as const, label: '历史' },
  { value: 'runs' as const, label: '运行' },
]
const canRun = computed(() => runnable(S.project, S.file))
const page = computed(() => (canRun.value ? S.fileTab : 'history'))
const runs = computed(() => [...runsOf(S.project, S.file)].reverse())
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
        <h3 class="t-eyebrow-read ot-fp__label">在改它的事</h3>
        <button
          v-for="t in onFile"
          :key="t.id"
          type="button"
          class="ot-fp__thing"
          :data-thing="t.id"
          @click="openThing(t.id, 'files', S.file)"
        >
          <CheeseAvatar v-if="doer(t) === 'cheese'" :size="20" name="芝士" handle="cheese" />
          <UserAvatar v-else :size="20" :name="NAMES[doer(t)]" :seed="doer(t)" />
          <span class="ot-fp__thing-title">{{ t.title }}</span>
          <span class="ot-fp__state" :class="{ 'is-wait': t.status === 'wait' }">{{ where(t) }}</span>
          <v-icon size="14" icon="mdi-chevron-right" class="ot-fp__go" />
        </button>
      </section>
      <div class="ot-fp__histhead">
        <SegmentedControl
          v-if="canRun"
          v-model="S.fileTab"
          :options="
            PAGES.map((x) => (x.value === 'runs' ? { ...x, label: runs.length ? `运行 ${runs.length}` : '运行' } : x))
          "
          label="历史或运行"
          size="sm"
          data-file-tab
        />
        <h3 v-else class="t-eyebrow-read ot-fp__label">历史</h3>
        <span class="ot-head__gap" />
        <SegmentedControl
          v-if="page === 'history'"
          v-model="scope"
          :options="SCOPES"
          label="历史的范围"
          size="sm"
          data-hist-scope
        />
      </div>
      <div v-if="page === 'runs'" class="ot-runs" data-zone="file-runs">
        <p v-if="!runs.length" class="ot-runs__empty">
          还没运行过。点文件上面的「运行」：在项目的环境里跑，输出记在这里。
        </p>
        <article v-for="r in runs" :key="r.id" class="ot-runs__rec" :data-run-rec="r.id">
          <div class="ot-runs__meta">
            <UserAvatar :size="18" :name="NAMES[r.who]" :seed="r.who" />
            <b>{{ NAMES[r.who] }}</b> · {{ hhmm(r.at) }} · 版本 <code>{{ r.ver }}</code>
            <span class="ot-head__gap" />
            <BaseButton
              v-if="!r.running"
              kind="ghost"
              size="sm"
              icon="mdi-send-outline"
              title="发到频道"
              aria-label="发到频道"
              @click="shareRun(S.project, S.file, r.id)"
            />
          </div>
          <div class="ot-runs__cmd">$ {{ r.cmd || '…' }}</div>
          <div v-if="r.running" class="ot-runs__out ot-runs__out--wait">运行中…</div>
          <pre v-else class="ot-runs__out">{{ r.out }}</pre>
          <div
            v-if="!r.running && r.expect !== undefined"
            class="ot-runs__verdict"
            :class="r.pass ? 'is-ok' : 'is-bad'"
          >
            {{ r.pass ? '通过：和样例输出一致' : `不通过：样例输出是 ${r.expect}` }}
          </div>
        </article>
      </div>
      <ol v-else class="ot-hist">
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

.ot-runs {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ot-runs__empty {
  margin: 4px 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-runs__rec {
  padding: 6px 8px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.ot-runs__meta {
  display: flex;
  gap: 4px;
  align-items: center;
  min-height: 28px;
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}

.ot-runs__meta b {
  color: var(--ink);
}

.ot-runs__meta code,
.ot-runs__cmd,
.ot-runs__out {
  font-family: var(--font-mono);
}

.ot-runs__cmd,
.ot-runs__out {
  font-size: 11px;
  line-height: 1.55;
}

.ot-runs__cmd {
  color: var(--muted);
  overflow-wrap: anywhere;
}

.ot-runs__out {
  max-height: 180px;
  margin: 2px 0 0;
  overflow: auto;
  color: var(--ink);
  white-space: pre-wrap;
}

.ot-runs__out--wait {
  color: var(--muted);
}

.ot-runs__verdict {
  margin-top: 4px;
  font-size: 12px;
}

.ot-runs__verdict.is-ok {
  color: var(--ok-ink);
}

.ot-runs__verdict.is-bad {
  color: var(--danger-ink);
}

.ot-hist__by {
  color: var(--muted);
}

.ot-hist__text b {
  color: var(--ink);
}
</style>
