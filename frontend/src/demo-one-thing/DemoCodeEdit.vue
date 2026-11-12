<script setup lang="ts">
// A code file in 项目文件: the live project, typed into directly, the same way
// a document is. Whoever else is in the file shows with their caret and face
// (王磊 in preprocess.py, 周珂 in a team's tsh.c); typing saves itself, and
// each saved stretch is one 历史 entry 「林晓 改了 …」 (state.editProject).
// While a 一件事 of 芝士's changes some lines of the file, those lines carry a
// passive marker, a band with the tag 「芝士在改这几行」, and the bar names the
// 一件事 (「芝士的「…」在改第 19 行 ›」), so people see where they might
// collide; it is not a lock, and either opens the 一件事 on 文件.
// 运行 is the file's own action, as 预览 is a document's: the project's
// environment runs the file with the command its 题目 brings (Data Lab's
// btest; a 题单 problem on 样例 1 / 样例 2 / 自定义输入, with 通过 / 不通过),
// or in the research project the one 设置「怎么运行」 names, else by type.
// The output is a run record on the file (who, when, which version); the
// latest folds out under the code, all of them are in the right panel's
// 运行, and 发到频道 posts one to the channel when someone wants to. A file
// mention clicked in a channel brings its lines into view with a band. A
// submission opened from its 题目's 提交记录 shows here read-only, pinned to
// its version.
// The surface is DemoCodeArea (why it is not Monaco is said there).
import type { LineTag, Peer } from './DemoCodeArea.vue'

import { computed, onBeforeUnmount, ref } from 'vue'

import { avatarColor } from '@/utils/avatar'

import { UNITS } from './course'
import { asContext, isClaim, NAMES, projectTeam } from './data'
import DemoCodeArea from './DemoCodeArea.vue'
import { hhmm } from './shapes'
import {
  cheeseMarks,
  editProject,
  fkey,
  openThing,
  presenceOf,
  projectText,
  resultOf,
  runInProject,
  runnable,
  runsOf,
  S,
  shareRun,
  unitOf,
} from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import ChangesDiff from '@/components/panels/ChangesDiff.vue'

const p = computed(() => S.project)
const path = computed(() => S.file)
const key = computed(() => fkey(p.value, path.value))
const text = computed({
  get: () => projectText(p.value, path.value),
  set: (v: string) => editProject(p.value, path.value, v),
})
const mark = computed(() => (S.codeAt?.path === path.value ? S.codeAt : null))
const saving = computed(() => !!S.saving[key.value])

// ---- who else is here: their carets ----
const spot = ref(0)
const timer = window.setInterval(() => (spot.value = (spot.value + 1) % 4), 2200)
onBeforeUnmount(() => window.clearInterval(timer))
/** 周珂 writing the built-ins in a team's tsh.c: where his caret goes as he works. */
const MATE_SPOTS = [31, 29, 31, 32]
const peers = computed<Peer[]>(() => {
  const n = text.value.split('\n').length
  const here = presenceOf(p.value, path.value).map((x) => ({
    handle: x.who,
    name: NAMES[x.who]!,
    color: avatarColor(x.who),
    line: Math.min(x.line, n),
    col: x.col,
  }))
  if (p.value === 'shelllab' && path.value === 'tsh.c' && projectTeam(p.value).members.includes('zhouke'))
    here.push({
      handle: 'zhouke',
      name: NAMES.zhouke!,
      color: avatarColor('zhouke'),
      line: Math.min(MATE_SPOTS[spot.value]!, n),
      col: 6,
    })
  return here
})

// ---- 芝士's 一件事 changing some lines ----
const marks = computed(() => cheeseMarks(p.value, path.value))
const tags = computed<LineTag[]>(() =>
  marks.value.map((m) => ({
    key: m.thing,
    start: m.start,
    end: m.end,
    label: m.who === 'cheese' ? '芝士在改这几行' : `${NAMES[m.who]}接手在改这几行`,
    color: avatarColor(m.who),
  }))
)
const openMark = (id: string) => openThing(id, 'files', path.value)

// ---- 运行 ----
const canRun = computed(() => runnable(p.value, path.value))
const unit = computed(() => unitOf(p.value, path.value))
const samples = computed(() => !!UNITS[unit.value ?? '']?.samples)
const runIn = computed(() => (S.runIn[key.value] ??= { pick: 0, text: '' }))
const PICKS = [
  { value: 0, label: '样例 1' },
  { value: 1, label: '样例 2' },
  { value: 'custom', label: '自定义输入' },
] as const
const runs = computed(() => runsOf(p.value, path.value))
const last = computed(() => runs.value.at(-1) ?? null)
const open = computed(() => S.runOpen[key.value] ?? false)

// ---- a pinned version, read-only ----
const sub = computed(() =>
  S.viewSub && isClaim(p.value) ? S.subs[p.value]?.find((x) => x.id === S.viewSub) ?? null : null
)
const pinned = computed(() => (sub.value ? asContext(path.value, sub.value.code.replace(/\n$/, '').split('\n')) : []))
</script>

<template>
  <div class="ot-ced">
    <template v-if="sub">
      <div class="ot-ced__bar ot-ced__bar--pinned">
        <v-icon size="15" icon="mdi-pin-outline" class="ot-ced__mark" />
        <span class="ot-ced__copy"
          >第 {{ sub.n }} 次提交的版本 <code>{{ sub.version }}</code> · {{ resultOf(sub).text }} · 只读</span
        >
        <span class="ot-head__gap" />
        <BaseButton kind="secondary" size="sm" prepend-icon="mdi-arrow-left" @click="S.viewSub = null"
          >回到现在的文件</BaseButton
        >
      </div>
      <div class="ot-ced__pinned"><ChangesDiff :lines="pinned" inline /></div>
    </template>

    <template v-else>
      <div class="ot-ced__bar" data-zone="code-bar">
        <span v-if="peers.length" class="ot-ced__faces" :title="`${peers.map((x) => x.name).join('、')}也在这个文件里`">
          <UserAvatar v-for="x in peers" :key="x.handle" :size="20" :name="x.name" :seed="x.handle" />
        </span>
        <span class="ot-ced__copy" data-zone="saved" title="自动保存，每段改动记进历史，能撤回">{{
          saving ? '保存中…' : '已保存'
        }}</span>
        <button
          v-for="m in marks"
          :key="m.thing"
          type="button"
          class="ot-ced__things"
          data-things-on-file
          title="打开那件事，看它改到哪了"
          @click="openMark(m.thing)"
        >
          <CheeseAvatar :size="14" name="芝士" handle="cheese" />{{ m.who === 'cheese' ? '芝士' : NAMES[m.who] }}的「{{
            m.title
          }}」在改第 {{ m.start }}{{ m.end > m.start ? `–${m.end}` : '' }} 行 ›
        </button>
        <span class="ot-head__gap" />
        <template v-if="canRun">
          <select v-if="samples" v-model="runIn.pick" class="ot-ced__insel" aria-label="运行的输入" data-run-input>
            <option v-for="o in PICKS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
          <BaseButton
            id="b-run"
            kind="primary"
            size="sm"
            prepend-icon="mdi-play"
            title="在项目的环境里跑这个文件，输出记在这个文件上"
            @click="runInProject(p, path)"
            >运行</BaseButton
          >
        </template>
      </div>
      <textarea
        v-if="canRun && samples && runIn.pick === 'custom'"
        v-model="runIn.text"
        class="ot-ced__stdin"
        rows="2"
        spellcheck="false"
        autocomplete="off"
        placeholder="自定义输入：程序从这里读，例如 3 4"
        aria-label="自定义输入"
        data-run-custom
      />
      <div class="ot-ced__edit">
        <DemoCodeArea v-model="text" :label="`编辑 ${path}`" :mark="mark" :peers="peers" :tags="tags" @tag="openMark" />
      </div>
      <section v-if="last" class="ot-ced__run" :class="{ 'is-open': open }" data-zone="last-run">
        <div class="ot-ced__runhead">
          <button
            type="button"
            class="ot-ced__fold"
            :aria-expanded="open"
            :title="open ? '收起' : '展开最近一次运行'"
            @click="S.runOpen[key] = !open"
          >
            <v-icon size="15" :icon="open ? 'mdi-chevron-down' : 'mdi-chevron-right'" />
            <b>最近一次运行</b>
            <span class="ot-ced__runmeta"
              >{{ NAMES[last.who] }} · {{ hhmm(last.at) }} · 版本 <code>{{ last.ver }}</code></span
            >
            <span v-if="last.running" class="ot-ced__runres">运行中…</span>
            <span
              v-else-if="last.expect !== undefined"
              class="ot-ced__runres"
              :class="last.pass ? 'is-ok' : 'is-bad'"
              >{{ last.pass ? '通过' : '不通过' }}</span
            >
            <span v-else-if="last.total" class="ot-ced__runres" :class="last.ok === last.total ? 'is-ok' : ''"
              >{{ last.ok }}/{{ last.total }} 通过</span
            >
          </button>
          <span class="ot-head__gap" />
          <BaseButton
            v-if="!last.running"
            kind="ghost"
            size="sm"
            prepend-icon="mdi-send-outline"
            title="把这次运行发到频道里（不会自动发）"
            data-share-run
            @click="shareRun(p, path, last.id)"
            >发到频道</BaseButton
          >
        </div>
        <div v-if="open" class="ot-ced__runbody">
          <div class="ot-ced__cmd">$ {{ last.cmd || '…' }}</div>
          <pre v-if="!last.running" class="ot-ced__out">{{ last.out }}</pre>
          <div
            v-if="!last.running && last.expect !== undefined"
            class="ot-ced__verdict"
            :class="last.pass ? 'is-ok' : 'is-bad'"
          >
            <v-icon size="14" :icon="last.pass ? 'mdi-check-circle-outline' : 'mdi-close-circle-outline'" />{{
              last.pass ? '通过：和样例输出一致' : `不通过：样例输出是 ${last.expect}`
            }}
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.ot-ced {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.ot-ced__bar {
  display: flex;
  flex: none;
  gap: 8px;
  align-items: center;
  height: 40px;
  padding: 0 8px 0 16px;
  font-size: 13px;
  background: var(--fill);
  border-bottom: 1px solid var(--line);
}

.ot-ced__mark {
  color: var(--muted);
}

.ot-ced__faces {
  display: inline-flex;
  flex: none;
}

.ot-ced__faces > * + * {
  margin-left: -6px;
  border-radius: 50%;
  box-shadow: 0 0 0 2px var(--fill);
}

/* A 一件事 changing this file: a quiet note, not an action; it opens the 一件事. */
.ot-ced__things {
  display: inline-flex;
  flex: 0 1 auto;
  gap: 4px;
  align-items: center;
  min-width: 0;
  height: 24px;
  padding: 0 8px;
  overflow: hidden;
  font: inherit;
  font-size: 12px;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.ot-ced__things:hover {
  color: var(--ink);
}

.ot-ced__copy {
  flex: none;
  color: var(--muted);
  white-space: nowrap;
}

.ot-ced__copy code {
  font-family: var(--font-mono);
  font-size: 12px;
}

.ot-ced__bar--pinned .ot-ced__copy {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
}

.ot-ced__bar > :not(.ot-ced__copy, .ot-head__gap, .ot-ced__things) {
  flex: none;
}

.ot-ced__insel {
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

.ot-ced__stdin {
  flex: none;
  padding: 4px 8px;
  margin: 6px 16px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink);
  resize: none;
  background: var(--fill);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  outline: none;
}

.ot-ced__edit,
.ot-ced__pinned {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}

.ot-ced__pinned {
  overflow: auto;
}

.ot-ced__pinned :deep(.diff-line--hunk) {
  display: none;
}

.ot-ced__pinned :deep(.diff-line > .diff-line__num:first-of-type) {
  display: none;
}

.ot-ced__pinned :deep(.diff-line__text) {
  white-space: pre;
}

/* The latest run, folded under the code: one line, or the output under it. */
.ot-ced__run {
  display: flex;
  flex: none;
  flex-direction: column;
  max-height: 240px;
  border-top: 1px solid var(--line);
}

.ot-ced__runhead {
  display: flex;
  flex: none;
  gap: 8px;
  align-items: center;
  height: 36px;
  padding: 0 8px 0 8px;
  background: var(--fill);
}

.ot-ced__fold {
  display: inline-flex;
  flex: 0 1 auto;
  gap: 6px;
  align-items: center;
  min-width: 0;
  overflow: hidden;
  padding: 2px 6px;
  font: inherit;
  font-size: 12px;
  color: var(--ink);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.ot-ced__fold b {
  white-space: nowrap;
}

.ot-ced__fold:hover {
  background: var(--line-2);
}

.ot-ced__runmeta {
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-ced__runmeta code {
  font-family: var(--font-mono);
}

.ot-ced__runres {
  color: var(--muted);
  white-space: nowrap;
}

.ot-ced__runres.is-ok,
.ot-ced__verdict.is-ok {
  color: var(--ok-ink);
}

.ot-ced__runres.is-bad,
.ot-ced__verdict.is-bad {
  color: var(--danger-ink);
}

.ot-ced__runbody {
  flex: 1;
  min-height: 0;
  padding: 6px 16px 10px;
  overflow: auto;
}

.ot-ced__cmd,
.ot-ced__out {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
}

.ot-ced__cmd {
  color: var(--muted);
  overflow-wrap: anywhere;
}

.ot-ced__out {
  margin: 0;
  color: var(--ink);
  white-space: pre-wrap;
}

.ot-ced__verdict {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  font-size: 12px;
}
</style>
