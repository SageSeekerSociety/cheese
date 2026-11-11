<script setup lang="ts">
// A project's code view, in every project: a place to read and edit files.
// Opening a code file opens it editable on 正式版, as a document is: what is
// typed is the file, a teammate's caret shows where they are in it, and each
// pause in typing becomes one 历史 entry with 撤回. There is no 「改代码」
// switch: a read-only first view would be a mode documents do not have, and
// 撤回 already makes a slip cheap.
// Every other change is a 一件事, and the editor only leads into one. With
// lines selected, 「在频道里问芝士」 takes them into the file's channel, where an
// @芝士 opens a 讨论 like any other. 「开一件事自己做」 (on the file's bar above,
// FilesCenter) opens a 一件事 that I do: the editor then types into its copy,
// under a banner 「你在「…」的副本里改 · 回到正式版」. Review and 采纳 happen in
// the 一件事, never here: while one changes this file the bar only says so
// (「有 1 件事在改这个文件 ›」, which opens it on 改动). With 正式版受保护 on,
// 正式版 is read-only and the bar offers 「开一件事来改」.
// 运行 follows 方案「运行在哪」 (variants.ts): T1 runs in the project's one
// environment and the bottom panel is labelled 项目环境; T2 has no 运行, only a
// hint; T3 is my own terminal (输出 / 终端, only I can see it). A 题单 problem
// runs like an OJ: beside 运行 a small picker, 样例 1 / 样例 2 / 自定义输入
// (custom opens a box in the bottom panel), and a 样例 run says 通过 or 不通过
// against its expected output; Data Lab and Shell Lab run their own tests.
// A 一件事 of 芝士's that I took over (接手) opens here like my own, its banner
// saying whose it is, with 还给芝士. The editor has
// no course control: a submission is made to the 题目 (the origin card in the
// sidebar). A submission opened from its 题目's 提交记录 shows here read-only.
// The editing surface is DemoCodeArea (why it is not Monaco is said there).
import type { Peer } from './DemoCodeArea.vue'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { avatarColor } from '@/utils/avatar'

import { UNITS } from './course'
import { asContext, isClaim } from './data'
import DemoCodeArea from './DemoCodeArea.vue'
import {
  askInChannel,
  codeText,
  fkey,
  giveBack,
  isSaving,
  leaveCopy,
  lockedMain,
  myCopy,
  openOwnThing,
  openThing,
  resultOf,
  runCode,
  S,
  snapshot,
  termRun,
  thingsOnFile,
  typeCode,
  unitOf,
} from './state'
import { V } from './variants'

import BaseButton from '@/components/base/BaseButton.vue'
import ChangesDiff from '@/components/panels/ChangesDiff.vue'
import DocPresence from '@/components/panels/doc/DocPresence.vue'

const p = computed(() => S.project)
const path = computed(() => S.file)
const k = computed(() => fkey(p.value, path.value))
/** The 一件事 whose copy I am typing in. */
const copy = computed(() => myCopy(p.value))
const locked = computed(() => lockedMain(p.value))
const text = computed({
  get: () => codeText(p.value, path.value),
  set: (v: string) => typeCode(p.value, path.value, v),
})
const saving = computed(() => isSaving(p.value, path.value))
const mark = computed(() => (S.codeAt?.path === path.value ? S.codeAt : null))
const sel = computed(() => (S.codeSel?.path === path.value ? S.codeSel : null))
/** The 一件事 changing this file (other than the one I am typing in). */
const others = computed(() => thingsOnFile(p.value, path.value).filter((t) => t.id !== copy.value?.id))
// Leaving the file closes its burst of typing into 历史 (this view is keyed to one file: by
// unmount time S.file already names the next one).
const mine = { p: S.project, path: S.file }
onBeforeUnmount(() => snapshot(mine.p, mine.path))
function onSelect(r: { start: number; end: number } | null) {
  S.codeSel = r ? { path: path.value, ...r } : null
}

// ---- a teammate on 正式版 (scripted): 王磊 in the research project's eval/preprocess.py, 周珂 in the
// team's tsh.c, writing the built-in commands. Their caret moves over the lines they are on. ----
const PEER_AT: Record<string, { handle: string; name: string; path: string; spots: { line: number; col: number }[] }> =
  {
    research: {
      handle: 'wanglei',
      name: '王磊',
      path: 'eval/preprocess.py',
      spots: [
        { line: 6, col: 8 },
        { line: 6, col: 22 },
        { line: 7, col: 8 },
        { line: 7, col: 25 },
        { line: 5, col: 4 },
      ],
    },
    shelllab: {
      handle: 'zhouke',
      name: '周珂',
      path: 'tsh.c',
      spots: [
        { line: 31, col: 4 },
        { line: 31, col: 18 },
        { line: 29, col: 8 },
        { line: 31, col: 26 },
        { line: 32, col: 2 },
      ],
    },
  }
const peer = computed(() => PEER_AT[p.value])
const spot = ref(0)
const peerTimer = window.setInterval(() => (spot.value = (spot.value + 1) % 5), 2200)
onBeforeUnmount(() => window.clearInterval(peerTimer))
const peerHere = computed(() => !!peer.value && !copy.value && path.value === peer.value.path)
const peers = computed<Peer[]>(() => {
  if (!peerHere.value) return []
  const n = text.value.split('\n').length
  const who = peer.value!
  const s = who.spots[spot.value]!
  return [{ handle: who.handle, name: who.name, color: avatarColor(who.handle), line: Math.min(s.line, n), col: s.col }]
})
const faces = computed(() =>
  peerHere.value
    ? [
        {
          clientId: 2,
          handle: peer.value!.handle,
          agent: false,
          name: peer.value!.name,
          avatar: '',
          color: avatarColor(peer.value!.handle),
        },
        { clientId: 3, handle: 'linxiao', agent: false, name: '林晓', avatar: '', color: avatarColor('linxiao') },
      ]
    : []
)

// ---- a pinned version, read-only ----
const sub = computed(() =>
  S.viewSub && isClaim(p.value) ? S.subs[p.value]?.find((x) => x.id === S.viewSub) ?? null : null
)
const pinned = computed(() => (sub.value ? asContext(path.value, sub.value.code.replace(/\n$/, '').split('\n')) : []))

// ---- 运行 and the bottom panel, per 方案「运行在哪」 ----
/** T2: people have no 运行. */
const noRun = computed(() => V.run === 'T2')
/** T3: my own terminal beside 输出. */
const ownTerm = computed(() => V.run === 'T3')
const open = ref(true)
/** A 题单 problem: 运行 takes 样例 1, 样例 2 or custom input. */
const samples = computed(() => !!UNITS[unitOf(p.value, path.value) ?? '']?.samples)
const runIn = computed(() => (S.runIn[k.value] ??= { pick: 0, text: '' }))
const PICKS = [
  { value: 0, label: '样例 1' },
  { value: 1, label: '样例 2' },
  { value: 'custom', label: '自定义输入' },
] as const
const out = computed(() => S.out[k.value])
const term = computed(() => S.term[p.value] ?? [])
const cmd = ref('')
const termBody = ref<HTMLElement | null>(null)
function enter() {
  termRun(p.value, path.value, cmd.value)
  cmd.value = ''
}
watch(
  () => [term.value.length, S.outTab],
  async () => {
    await nextTick()
    termBody.value?.scrollTo({ top: termBody.value.scrollHeight })
  }
)

const save = () => snapshot(p.value, path.value, false)
function run() {
  snapshot(p.value, path.value)
  open.value = true
  runCode(p.value, path.value)
}
const PTABS = [
  { key: 'out', label: '输出' },
  { key: 'term', label: '终端' },
] as const
function showTab(t: 'out' | 'term') {
  S.outTab = t
  open.value = true
}
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
          >回到正在改的</BaseButton
        >
      </div>
      <div class="ot-ced__pinned"><ChangesDiff :lines="pinned" inline /></div>
    </template>

    <template v-else>
      <div v-if="copy" class="ot-ced__bar ot-ced__bar--copy" data-zone="copy">
        <v-icon size="15" icon="mdi-source-fork" class="ot-ced__mark" />
        <span v-if="copy.takenBy" class="ot-ced__copy" :title="`你接手了芝士的「${copy.title}」`"
          >你接手了芝士的「<b>{{ copy.title }}</b
          >」</span
        >
        <button v-if="copy.takenBy" id="b-give-back" type="button" class="ot-ced__back" @click="giveBack(copy.id)">
          · 还给芝士
        </button>
        <span v-if="!copy.takenBy" class="ot-ced__copy" :title="`你在「${copy.title}」的副本里改`"
          >你在「<b>{{ copy.title }}</b
          >」的副本里改</span
        >
        <button v-if="!copy.takenBy" id="b-leave-copy" type="button" class="ot-ced__back" @click="leaveCopy">
          · 回到正式版
        </button>
        <span class="ot-head__gap" />
        <!-- The banner's own words need the room: 看这件事 and 运行 are icons here. -->
        <BaseButton
          id="b-see-thing"
          kind="ghost"
          size="sm"
          icon="mdi-checkbox-marked-outline"
          title="看这件事"
          aria-label="看这件事"
          @click="openThing(copy.id, 'changes')"
        />
        <BaseButton
          v-if="!noRun"
          id="b-run"
          kind="ghost"
          size="sm"
          icon="mdi-play"
          title="运行这份副本"
          aria-label="运行这份副本"
          @click="run"
        />
      </div>
      <div v-else class="ot-ced__bar">
        <v-icon size="15" :icon="locked ? 'mdi-shield-lock-outline' : 'mdi-file-code-outline'" class="ot-ced__mark" />
        <span
          class="ot-ced__copy"
          data-zone="saved"
          :title="locked ? '改动都要走一件事，正式版只读' : '改了就生效；每次改动自动记进历史，能撤回'"
          >{{ locked ? '正式版受保护' : saving ? '正式版 · 正在改…' : '正式版' }}</span
        >
        <DocPresence v-if="faces.length" :peers="faces" class="ot-ced__faces" />
        <button
          v-if="others.length"
          type="button"
          class="ot-ced__things"
          data-things-on-file
          title="在那件事的「改动」里看"
          @click="openThing(others[0]!.id, 'changes')"
        >
          有 {{ others.length }} 件事在改这个文件 ›
        </button>
        <span class="ot-head__gap" />
        <BaseButton
          v-if="sel"
          id="b-ask-chan"
          kind="ghost"
          size="sm"
          prepend-icon="mdi-message-text-outline"
          :title="`带着第 ${sel.start}${sel.end > sel.start ? `–${sel.end}` : ''} 行去频道里问`"
          @click="askInChannel"
          >在频道里问芝士</BaseButton
        >
        <BaseButton
          v-if="locked"
          id="b-own"
          kind="primary"
          size="sm"
          prepend-icon="mdi-checkbox-marked-outline"
          @click="openOwnThing"
          >开一件事来改</BaseButton
        >
        <span
          v-if="noRun"
          class="ot-ced__norun"
          data-zone="no-run"
          :title="isClaim(p) ? '这门课里芝士只给提示，不替你运行' : '选中代码，在频道里问芝士，它在一件事里跑'"
          ><v-icon size="14" icon="mdi-play-off" />{{ isClaim(p) ? '没有运行' : '运行交给芝士' }}</span
        >
        <template v-else>
          <select v-if="samples" v-model="runIn.pick" class="ot-ced__insel" aria-label="运行的输入" data-run-input>
            <option v-for="o in PICKS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
          <BaseButton id="b-run" kind="secondary" size="sm" prepend-icon="mdi-play" @click="run">运行</BaseButton>
        </template>
      </div>
      <div class="ot-ced__edit">
        <DemoCodeArea
          v-model="text"
          :label="`编辑 ${path}`"
          :readonly="locked"
          :peers="peers"
          :mark="mark"
          @save="save"
          @blur="snapshot(p, path)"
          @select="onSelect"
        />
      </div>
    </template>

    <p v-if="noRun" class="ot-ced__runhint" data-zone="terminal">
      <v-icon size="14" icon="mdi-information-outline" />{{
        isClaim(p)
          ? '这里没有「运行」，这门课里芝士也只给提示：代码要等提交后由评测机跑。'
          : '这里没有「运行」：选中代码，在频道里问芝士，它在一件事里跑，输出在那件事的「现场」。'
      }}
    </p>
    <div v-else class="ot-ced__panel" :class="{ 'is-open': open }" data-zone="terminal">
      <div class="ot-ced__ptabs">
        <template v-if="ownTerm">
          <button
            v-for="t in PTABS"
            :key="t.key"
            type="button"
            class="ot-ced__ptab"
            :class="{ 'is-on': S.outTab === t.key && open }"
            @click="showTab(t.key)"
          >
            {{ t.label }}
          </button>
        </template>
        <button v-else type="button" class="ot-ced__ptab" :class="{ 'is-on': open }" @click="showTab('out')">
          输出
        </button>
        <span class="ot-head__gap" />
        <span v-if="ownTerm" class="ot-ced__mine"
          ><v-icon size="13" icon="mdi-lock-outline" />你的环境 · 只有你能看到</span
        >
        <span v-else class="ot-ced__mine" data-zone="project-env"
          ><v-icon size="13" icon="mdi-server-outline" />项目环境{{
            copy ? ` · 「${copy.title}」的副本` : ' · 芝士做事也在这里'
          }}</span
        >
        <BaseButton
          kind="ghost"
          size="sm"
          :icon="open ? 'mdi-chevron-down' : 'mdi-chevron-up'"
          :title="open ? '收起' : '展开'"
          :aria-label="open ? '收起' : '展开'"
          @click="open = !open"
        />
      </div>
      <div v-if="open && (S.outTab === 'out' || !ownTerm)" class="ot-ced__pbody" role="status">
        <textarea
          v-if="samples && runIn.pick === 'custom'"
          v-model="runIn.text"
          class="ot-ced__stdin"
          rows="2"
          spellcheck="false"
          autocomplete="off"
          placeholder="自定义输入：程序从这里读，例如 3 4"
          aria-label="自定义输入"
          data-run-custom
        />
        <div v-if="!out" class="ot-ced__hint">
          {{
            ownTerm ? '点「运行」，在你自己的环境里跑这份代码' : '点「运行」，在项目环境里跑这份代码；每次运行记进历史'
          }}
        </div>
        <div v-else-if="out.kind === 'running'" class="ot-ced__cmd">运行中…</div>
        <template v-else>
          <div class="ot-ced__cmd">$ {{ out.cmd }}</div>
          <pre class="ot-ced__res">{{ out.out }}</pre>
          <div
            v-if="out.expect !== undefined"
            class="ot-ced__verdict"
            :class="out.pass ? 'is-ok' : 'is-bad'"
            data-run-verdict
          >
            <v-icon size="14" :icon="out.pass ? 'mdi-check-circle-outline' : 'mdi-close-circle-outline'" />{{
              out.pass ? '通过：和样例输出一致' : `不通过：样例输出是 ${out.expect}`
            }}
          </div>
        </template>
      </div>
      <div v-else-if="open" ref="termBody" class="ot-ced__pbody ot-ced__term">
        <template v-for="(t, i) in term" :key="i">
          <div class="ot-ced__cmd">$ {{ t.cmd }}</div>
          <pre class="ot-ced__res">{{ t.out }}</pre>
        </template>
        <label class="ot-ced__prompt"
          >$
          <input
            v-model="cmd"
            class="ot-ced__input"
            spellcheck="false"
            autocomplete="off"
            aria-label="终端"
            @keydown.enter="enter"
        /></label>
      </div>
    </div>
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

/* In a 一件事's copy: the bar says so in the accent wash, the way a 一件事 being worked on reads. */
.ot-ced__bar--copy {
  gap: 6px;
  padding-left: 12px;
  background: var(--accent-wash);
}

.ot-ced__bar--copy .ot-ced__mark {
  color: var(--accent-ink);
}

.ot-ced__copy b {
  font-weight: 600;
}

.ot-ced__back {
  padding: 0;
  font: inherit;
  color: var(--accent-ink);
  white-space: nowrap;
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-ced__back:hover {
  text-decoration: underline;
}

/* A 一件事 changing this file: a quiet note, not an action; it opens the 一件事. With lines selected
   the bar also holds 在频道里问芝士, and the note gives way first. */
.ot-ced__things {
  flex: 0 1 auto;
  min-width: 0;
  height: 24px;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 0 8px;
  font: inherit;
  font-size: 12px;
  color: var(--text);
  white-space: nowrap;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.ot-ced__things:hover {
  color: var(--ink);
}

.ot-ced__norun {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 0 4px;
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
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
  display: block;
  width: 100%;
  padding: 4px 8px;
  margin: 4px 0 6px;
  font: inherit;
  color: var(--ink);
  resize: vertical;
  background: var(--fill);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  outline: none;
}

.ot-ced__verdict {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  font-family: var(--font-sans);
  font-size: 12px;
}

.ot-ced__verdict.is-ok {
  color: var(--ok-ink);
}

.ot-ced__verdict.is-bad {
  color: var(--danger-ink);
}

.ot-ced__runhint {
  display: flex;
  flex: none;
  gap: 6px;
  align-items: center;
  padding: 8px 16px;
  margin: 0;
  font-size: 12px;
  color: var(--muted);
  border-top: 1px solid var(--line);
}

.ot-ced__copy {
  /* 「正式版」 always reads in full; a longer label gives way after it. */
  min-width: 3em;
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-ced__copy code {
  font-family: var(--font-mono);
  font-size: 12px;
}

.ot-ced__bar > :not(.ot-ced__copy, .ot-head__gap, .ot-ced__things) {
  flex: none;
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

.ot-ced__panel {
  display: flex;
  flex: none;
  flex-direction: column;
  border-top: 1px solid var(--line);
}

.ot-ced__panel.is-open {
  height: 196px;
}

.ot-ced__ptabs {
  display: flex;
  flex: none;
  gap: 4px;
  align-items: center;
  height: 32px;
  padding: 0 4px 0 8px;
}

.ot-ced__ptab {
  height: 24px;
  padding: 0 8px;
  font: inherit;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.ot-ced__ptab:hover {
  color: var(--ink);
}

.ot-ced__ptab.is-on {
  color: var(--ink);
  background: var(--fill-2);
}

.ot-ced__mine {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  font-size: 12px;
  color: var(--muted);
}

.ot-ced__pbody {
  flex: 1;
  min-height: 0;
  padding: 4px 16px 8px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
}

.ot-ced__hint {
  font-family: var(--font-sans);
  color: var(--muted);
}

.ot-ced__cmd {
  color: var(--muted);
}

.ot-ced__res {
  margin: 0 0 4px;
  font: inherit;
  color: var(--ink);
  white-space: pre-wrap;
}

.ot-ced__term {
  cursor: text;
}

.ot-ced__prompt {
  display: flex;
  gap: 6px;
  color: var(--muted);
}

.ot-ced__input {
  flex: 1;
  min-width: 0;
  font: inherit;
  color: var(--ink);
  background: none;
  border: 0;
  outline: none;
  caret-color: var(--ink);
}
</style>
