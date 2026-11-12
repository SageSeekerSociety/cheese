<script setup lang="ts">
// A 一件事's 工作台: the 一件事's own copy of the files it touches, what it
// made, and every 运行 made in it with what it printed. It is the same place
// whoever does the 一件事. While 芝士 does it, it is live and read-only: the
// file 芝士 is in opens by itself, a caret named 芝士 moves over the line it is
// typing, a dot marks that file in the list, and each command it runs lands
// in the strip below with its output. While a person does it (「改这个文件」,
// after 接手, a course's 一件事, a team doing one together) the same 工作台 is
// editable: the copy is typed into, 运行 runs the open file of the copy, and
// the output stays in the 一件事. A 题单 problem runs like an OJ: 样例 1 /
// 样例 2 / 自定义输入, and a 样例 run says 通过 or 不通过 against its expected
// output; Data Lab and Shell Lab run their own tests. 正式版 never changes
// here: that is 采纳, in the 一件事's 改动. 接手 / 交给芝士 / 改好了 sit in the
// 一件事's header (ThingActions): in ThingPanel, or under W1 in this 工作台's
// own header; the bar on top says who is doing it and whether this 工作台
// can be typed into.
// Where it is drawn is 方案「工作台放哪」 (variants.ts): `center` (W1) takes the
// center column, with a back arrow to the channel; `panel` (W2) sits under the
// 一件事's tabs in the right panel, its files a row of tabs on top; `full` (W3)
// fills the 一件事's own view. Both wide ones list the files at the left.
import type { Peer } from './DemoCodeArea.vue'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { avatarColor } from '@/utils/avatar'

import { UNITS } from './course'
import { channelOf, NAMES, projectOfChan } from './data'
import DemoCodeArea from './DemoCodeArea.vue'
import { hhmm } from './shapes'
import { benchFileOf, benchFiles, canWork, doingNow, runInBench, S, t4Plan, thingById, typeCopy, unitOf } from './state'
import ThingActions from './ThingActions.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { fileIcon } from '@/lib/fileKind'

const props = defineProps<{ mode: 'center' | 'panel' | 'full' }>()

const t = computed(() => thingById(S.thing)!)
const p = computed(() => projectOfChan(t.value.chan))
const files = computed(() => benchFiles(t.value))
const file = computed(() => benchFileOf(t.value))
const added = computed(() => !!file.value && file.value in (t.value.adds ?? {}))
const mine = computed(() => canWork(t.value))
const who = computed(() => doingNow(t.value))
const editable = computed(() => mine.value && !added.value && !!file.value)
const text = computed({
  get: () => (file.value ? t.value.copy?.[file.value]?.text ?? '' : ''),
  set: (v: string) => file.value && typeCopy(t.value.id, file.value, v),
})
const changed = (path: string) => {
  const c = t.value.copy?.[path]
  return c ? c.text !== c.base : true
}
/** The file 芝士 is typing in now, in this 一件事. */
const live = computed(() => (S.cheeseAt?.thing === t.value.id && who.value === 'cheese' ? S.cheeseAt : null))

function pick(path: string) {
  S.benchFile[t.value.id] = path
}

// ---- carets: 芝士's while it types; a teammate's in a team's 一件事 (周珂 in tsh.c, writing the built-ins) ----
const spot = ref(0)
const timer = window.setInterval(() => (spot.value = (spot.value + 1) % 4), 2200)
onBeforeUnmount(() => window.clearInterval(timer))
const MATE_SPOTS = [31, 29, 31, 32]
const peers = computed<Peer[]>(() => {
  const n = text.value.split('\n').length
  if (live.value && live.value.path === file.value) {
    const line = Math.min(live.value.line, n)
    // At the line's first character: the end of a long line can sit past the editor's right edge.
    const col = /^\s*/.exec(text.value.split('\n')[line - 1] ?? '')![0].length
    return [{ handle: 'cheese', name: '芝士', color: avatarColor('cheese'), line, col }]
  }
  if (t.value.team?.includes('zhouke') && file.value === 'tsh.c' && t.value.status === 'run')
    return [
      {
        handle: 'zhouke',
        name: NAMES.zhouke!,
        color: avatarColor('zhouke'),
        line: Math.min(MATE_SPOTS[spot.value]!, n),
        col: 6,
      },
    ]
  return []
})

/** What the bar on top says: who is doing it now, and whether this 工作台 can be typed into. */
const state = computed(() => {
  const th = t.value
  if (th.status === 'wait') return '做完了，等采纳 · 只读'
  if (th.status === 'done') return '已采纳，进了正式版 · 只读'
  if (who.value === 'cheese')
    return live.value ? `芝士在改 ${live.value.path} 第 ${live.value.line} 行 · 只读` : '芝士在做 · 只读'
  if (th.team && mine.value)
    return `${th.team.map((h) => NAMES[h]).join('、')}一起在做 · 改的是这件事的副本，正式版不动`
  if (mine.value) return '你在做 · 改的是这件事的副本，正式版不动'
  return `${NAMES[who.value ?? '']}在做 · 只读`
})

// ---- 运行 ----
const runs = computed(() => t.value.runs ?? [])
const unit = computed(() => (file.value ? unitOf(p.value, file.value) : undefined))
const samples = computed(() => !!UNITS[unit.value ?? '']?.samples)
const inKey = computed(() => `${t.value.id}:${file.value}`)
const runIn = computed(() => (S.runIn[inKey.value] ??= { pick: 0, text: '' }))
const PICKS = [
  { value: 0, label: '样例 1' },
  { value: 1, label: '样例 2' },
  { value: 'custom', label: '自定义输入' },
] as const
const strip = ref<HTMLElement | null>(null)
watch(
  () => [runs.value.length, runs.value.at(-1)?.running],
  async () => {
    await nextTick()
    // The newest run from its first line: a long output (btest's) is read from the top.
    const last = strip.value?.lastElementChild as HTMLElement | null | undefined
    if (strip.value && last) strip.value.scrollTop = last.offsetTop - strip.value.offsetTop - 4
  },
  { immediate: true }
)
function run() {
  if (file.value) runInBench(t.value.id, file.value)
}

const chanTitle = computed(() => channelOf(t.value.chan)?.title ?? '')
</script>

<template>
  <section class="ot-wb" :class="`ot-wb--${props.mode}`" data-zone="bench">
    <header v-if="props.mode === 'center'" class="ot-wb__head">
      <BaseButton
        id="b-bench-back"
        kind="ghost"
        size="sm"
        prepend-icon="mdi-arrow-left"
        :title="`回到 # ${chanTitle}`"
        @click="S.bench = false"
        ># {{ chanTitle }}</BaseButton
      >
      <span class="ot-wb__slash">/</span>
      <h2 class="ot-wb__title" :title="t.title">{{ t.title }}</h2>
      <span class="ot-wb__name">工作台</span>
      <span class="ot-head__gap" />
      <ThingActions quiet />
    </header>

    <div class="ot-wb__bar" :class="{ 'is-mine': mine, 'is-live': who === 'cheese' }" data-bench-state>
      <CheeseAvatar v-if="who === 'cheese'" :size="18" name="芝士" handle="cheese" />
      <UserAvatar v-else-if="who" :size="18" :name="NAMES[who]" :seed="who" />
      <v-icon v-else size="15" icon="mdi-lock-outline" />
      <span class="ot-wb__state">{{ state }}</span>
      <span v-if="who === 'cheese'" class="ot-wb__pulse" aria-hidden="true" />
    </div>

    <div class="ot-wb__main">
      <nav v-if="files.length" class="ot-wb__files" aria-label="这件事的文件">
        <span v-if="props.mode !== 'panel'" class="t-eyebrow-read ot-wb__label">这件事的文件</span>
        <button
          v-for="f in files"
          :key="f"
          type="button"
          class="ot-wb__file"
          :class="{ 'is-on': f === file }"
          :title="f"
          :data-bench-file="f"
          @click="pick(f)"
        >
          <v-icon size="15" :icon="fileIcon(f)" />
          <span class="ot-wb__fname">{{ f.split('/').pop() }}</span>
          <span v-if="live?.path === f" class="ot-wb__dot ot-wb__dot--live" title="芝士在改" />
          <span v-else-if="changed(f)" class="ot-wb__dot" :title="f in (t.adds ?? {}) ? '新的' : '改了'" />
        </button>
        <div v-if="t.id === 't4' && props.mode !== 'panel'" class="ot-wb__plan" data-bench-plan>
          <span class="t-eyebrow-read ot-wb__label">计划</span>
          <div v-for="(s, i) in t4Plan()" :key="i" class="ot-wb__step" :class="{ 'is-done': s.done }">
            <v-icon size="14" :icon="s.done ? 'mdi-check-circle' : 'mdi-circle-outline'" />{{ s.subject }}
          </div>
        </div>
      </nav>

      <div class="ot-wb__view">
        <div v-if="!file" class="ot-wb__empty">这件事没有改文件，做了什么看下面的运行。</div>
        <div v-else-if="added" class="ot-wb__md"><MarkdownView :source="t.adds![file]!" /></div>
        <DemoCodeArea
          v-else
          :key="`${t.id}:${file}`"
          v-model="text"
          :label="editable ? `编辑 ${file}（这件事的副本）` : `${file}（只读）`"
          :readonly="!editable"
          :peers="peers"
        />
      </div>
    </div>

    <div class="ot-wb__strip" data-zone="bench-runs">
      <div class="ot-wb__striphead">
        <span class="ot-wb__striptitle">运行</span>
        <span class="ot-wb__count">{{ runs.length ? `${runs.length} 次` : '' }}</span>
        <span class="ot-head__gap" />
        <template v-if="editable">
          <select v-if="samples" v-model="runIn.pick" class="ot-wb__insel" aria-label="运行的输入" data-run-input>
            <option v-for="o in PICKS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
          <BaseButton id="b-run" kind="secondary" size="sm" prepend-icon="mdi-play" @click="run">运行</BaseButton>
        </template>
      </div>
      <textarea
        v-if="editable && samples && runIn.pick === 'custom'"
        v-model="runIn.text"
        class="ot-wb__stdin"
        rows="2"
        spellcheck="false"
        autocomplete="off"
        placeholder="自定义输入：程序从这里读，例如 3 4"
        aria-label="自定义输入"
        data-run-custom
      />
      <div ref="strip" class="ot-wb__runs" role="log">
        <div v-if="!runs.length" class="ot-wb__hint">
          {{ editable ? '点「运行」，在这件事里跑这份副本；输出留在这件事里。' : '还没有运行。' }}
        </div>
        <div v-for="r in runs" :key="r.id" class="ot-wb__run" :data-run="r.who">
          <div class="ot-wb__runmeta">
            <CheeseAvatar v-if="r.who === 'cheese'" :size="14" name="芝士" handle="cheese" />
            <UserAvatar v-else :size="14" :name="NAMES[r.who]" :seed="r.who" />
            {{ NAMES[r.who] }} · {{ hhmm(r.at) }}<template v-if="r.path"> · {{ r.path }}</template>
          </div>
          <div class="ot-wb__cmd">$ {{ r.cmd || '…' }}</div>
          <div v-if="r.running" class="ot-wb__out ot-wb__out--wait">运行中…</div>
          <template v-else>
            <pre class="ot-wb__out">{{ r.out }}</pre>
            <div
              v-if="r.expect !== undefined"
              class="ot-wb__verdict"
              :class="r.pass ? 'is-ok' : 'is-bad'"
              data-run-verdict
            >
              <v-icon size="14" :icon="r.pass ? 'mdi-check-circle-outline' : 'mdi-close-circle-outline'" />{{
                r.pass ? '通过：和样例输出一致' : `不通过：样例输出是 ${r.expect}`
              }}
            </div>
          </template>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ot-wb {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.ot-wb__head {
  display: flex;
  flex: none;
  gap: 6px;
  align-items: center;
  height: 48px;
  padding: 0 16px 0 8px;
  border-bottom: 1px solid var(--line);
}

.ot-wb__slash {
  color: var(--faint);
}

.ot-wb__title {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ot-wb__head > :not(.ot-wb__title, .ot-head__gap) {
  flex: none;
}

.ot-wb__name {
  flex: none;
  font-size: 13px;
  color: var(--muted);
}

/* Who is doing it: neutral while it is read-only; the accent wash while it is mine to type in, as a 一件事
   being worked on reads elsewhere. */
.ot-wb__bar {
  display: flex;
  flex: none;
  gap: 8px;
  align-items: center;
  height: 36px;
  padding: 0 16px;
  font-size: 13px;
  color: var(--muted);
  background: var(--fill);
  border-bottom: 1px solid var(--line);
}

.ot-wb__bar.is-mine {
  color: var(--accent-ink);
  background: var(--accent-wash);
}

.ot-wb__state {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-wb__bar.is-mine .ot-wb__state {
  color: var(--accent-ink);
}

.ot-wb__pulse {
  flex: none;
  width: 7px;
  height: 7px;
  background: var(--ok);
  border-radius: 50%;
  animation: ot-wb-pulse 1.4s var(--ease-standard) infinite;
}

@keyframes ot-wb-pulse {
  50% {
    opacity: 0.3;
  }
}

.ot-wb__main {
  display: flex;
  flex: 1;
  min-height: 0;
}

.ot-wb__files {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 1px;
  width: 160px;
  padding: 8px;
  overflow: auto;
  border-right: 1px solid var(--line);
}

/* In the right panel the files are a row of tabs over the file. */
.ot-wb--panel .ot-wb__main {
  flex-direction: column;
}

.ot-wb--panel .ot-wb__files {
  flex-direction: row;
  gap: 4px;
  width: auto;
  padding: 6px 8px;
  overflow-x: auto;
  border-right: 0;
  border-bottom: 1px solid var(--line);
}

.ot-wb__label {
  padding: 4px 6px;
}

.ot-wb__file {
  display: flex;
  flex: none;
  gap: 6px;
  align-items: center;
  height: 30px;
  padding: 0 8px 0 6px;
  font: inherit;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
}

.ot-wb__file:hover {
  background: var(--fill);
}

.ot-wb__file.is-on {
  font-weight: 600;
  color: var(--ink);
  background: var(--line-2);
}

.ot-wb__file .v-icon {
  flex: none;
  color: var(--muted);
}

.ot-wb__fname {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-wb__dot {
  flex: none;
  width: 6px;
  height: 6px;
  background: var(--accent-ink);
  border-radius: 50%;
}

.ot-wb__dot--live {
  background: var(--ok);
  animation: ot-wb-pulse 1.4s var(--ease-standard) infinite;
}

.ot-wb__plan {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 12px;
  margin-top: 8px;
  border-top: 1px solid var(--line);
}

.ot-wb__step {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  padding: 0 6px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-wb__step .v-icon {
  flex: none;
  margin-top: 1px;
}

.ot-wb__step.is-done {
  color: var(--text);
}

.ot-wb__step.is-done .v-icon {
  color: var(--ok);
}

.ot-wb__view {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.ot-wb__md {
  flex: 1;
  min-height: 0;
  padding: 16px 24px;
  overflow: auto;
}

.ot-wb__empty {
  padding: 24px;
  font-size: 13px;
  color: var(--muted);
}

.ot-wb__strip {
  display: flex;
  flex: none;
  flex-direction: column;
  height: 208px;
  border-top: 1px solid var(--line);
}

.ot-wb--panel .ot-wb__strip {
  height: 184px;
}

.ot-wb__striphead {
  display: flex;
  flex: none;
  gap: 8px;
  align-items: center;
  height: 36px;
  padding: 0 8px 0 16px;
}

.ot-wb__striptitle {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
}

.ot-wb__count {
  font-size: 12px;
  color: var(--faint);
}

.ot-wb__insel {
  height: 28px;
  padding: 0 6px;
  font: inherit;
  font-size: 12px;
  color: var(--ink);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.ot-wb__stdin {
  flex: none;
  padding: 4px 8px;
  margin: 0 16px 6px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink);
  resize: none;
  background: var(--fill);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  outline: none;
}

.ot-wb__runs {
  flex: 1;
  min-height: 0;
  padding: 0 16px 8px;
  overflow: auto;
}

.ot-wb__hint {
  font-size: 12px;
  color: var(--muted);
}

.ot-wb__run + .ot-wb__run {
  padding-top: 6px;
  margin-top: 6px;
  border-top: 1px dashed var(--line);
}

.ot-wb__runmeta {
  display: flex;
  gap: 4px;
  align-items: center;
  font-size: 11px;
  color: var(--faint);
}

.ot-wb__cmd,
.ot-wb__out {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
}

.ot-wb__cmd {
  color: var(--muted);
  overflow-wrap: anywhere;
}

.ot-wb__out {
  margin: 0;
  color: var(--ink);
  white-space: pre-wrap;
}

.ot-wb__out--wait {
  color: var(--muted);
}

.ot-wb__verdict {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  font-size: 12px;
}

.ot-wb__verdict.is-ok {
  color: var(--ok-ink);
}

.ot-wb__verdict.is-bad {
  color: var(--danger-ink);
}

@media (prefers-reduced-motion: reduce) {
  .ot-wb__pulse,
  .ot-wb__dot--live {
    animation: none;
  }
}
</style>
