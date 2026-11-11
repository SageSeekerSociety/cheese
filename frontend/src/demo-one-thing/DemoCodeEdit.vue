<script setup lang="ts">
// A project's code view, in every project: the code, 运行, and a bottom panel
// bound to my own environment (输出 and 终端, only I can see it; it is my
// terminal, not 芝士's). Opening a code file opens it editable on 正式版, as a
// document is: what is typed is the file, a teammate's caret shows where they
// are in it, and each pause in typing becomes one 历史 entry with 撤回. There is
// no 「改代码」 switch: a read-only first view would be a mode documents do not
// have, and 撤回 already makes a slip cheap. The editor is read-only only when
// a shared project has 正式版受保护 on and no branch is open.
// 开分支 (in a project with more than one person: the research project, a team's
// 题目) gives a person a copy of their own; the bar then names the
// branch, with 请人看一下 and 合回正式版. The editor is the same in every
// project and has no course control: a submission is made to the 题目 (the
// origin card in the sidebar), not from here. A submission opened from its
// 题目's 提交记录 shows here read-only: the version it pinned.
// The editing surface is DemoCodeArea (why it is not Monaco is said there).
import type { Peer } from './DemoCodeArea.vue'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { avatarColor } from '@/utils/avatar'

import { asContext, isClaim, isShared, NAMES } from './data'
import DemoCodeArea from './DemoCodeArea.vue'
import {
  askReview,
  branchChanges,
  canMerge,
  codeText,
  dropBranch,
  fkey,
  isSaving,
  lockedMain,
  mergeBranch,
  onBranch,
  openBranch,
  resultOf,
  reviewerOf,
  runCode,
  S,
  snapshot,
  termRun,
  typeCode,
} from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import ChangesDiff from '@/components/panels/ChangesDiff.vue'
import DocPresence from '@/components/panels/doc/DocPresence.vue'

const p = computed(() => S.project)
const path = computed(() => S.file)
const k = computed(() => fkey(p.value, path.value))
const shared = computed(() => isShared(p.value))
const reviewer = computed(() => NAMES[reviewerOf(p.value)])
const branch = computed(() => (onBranch(p.value) ? S.branch : null))
const locked = computed(() => lockedMain(p.value))
const text = computed({
  get: () => codeText(p.value, path.value),
  set: (v: string) => typeCode(p.value, path.value, v),
})
const saving = computed(() => isSaving(p.value, path.value))
const changes = computed(() => branchChanges())
const mark = computed(() => (S.codeAt?.path === path.value ? S.codeAt : null))
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
const peerHere = computed(() => !!peer.value && !branch.value && path.value === peer.value.path)
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

// ---- the bottom panel ----
const open = ref(true)
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
      <div v-if="branch" class="ot-ced__bar ot-ced__bar--branch" data-zone="branch">
        <v-icon size="15" icon="mdi-source-branch" class="ot-ced__mark" />
        <b class="ot-ced__branch">分支：{{ branch.name }}</b>
        <span class="ot-ced__copy">{{
          changes.length ? `改了 ${changes.length} 个文件` : '还没改，正式版不受影响'
        }}</span>
        <span v-if="branch.approved" class="ot-ced__ok"><v-icon size="14" icon="mdi-check" />{{ reviewer }}看过了</span>
        <span class="ot-head__gap" />
        <!-- The branch's own actions need the room: 放弃 and 运行 are icons here. -->
        <BaseButton
          kind="ghost"
          size="sm"
          icon="mdi-close"
          title="放弃这个分支"
          aria-label="放弃这个分支"
          @click="dropBranch"
        />
        <BaseButton id="b-run" kind="ghost" size="sm" icon="mdi-play" title="运行" aria-label="运行" @click="run" />
        <BaseButton
          v-if="!branch.approved"
          id="b-review"
          kind="secondary"
          size="sm"
          :disabled="!changes.length || branch.asked"
          @click="askReview"
          >{{ branch.asked ? `等${reviewer}看…` : '请人看一下' }}</BaseButton
        >
        <BaseButton
          id="b-merge-branch"
          kind="primary"
          size="sm"
          :disabled="!canMerge()"
          :title="S.protectMain[p] && !branch.approved ? '正式版受保护：要别人看过才能合' : ''"
          @click="mergeBranch"
          >合回正式版</BaseButton
        >
      </div>
      <div v-else-if="locked" class="ot-ced__bar" data-zone="locked">
        <v-icon size="15" icon="mdi-shield-lock-outline" class="ot-ced__mark" />
        <span class="ot-ced__copy">正式版受保护 · 人也要走分支，合之前要别人看过</span>
        <span class="ot-head__gap" />
        <BaseButton id="b-run" kind="secondary" size="sm" prepend-icon="mdi-play" @click="run">运行</BaseButton>
        <BaseButton id="b-branch" kind="primary" size="sm" prepend-icon="mdi-source-branch" @click="openBranch"
          >开分支</BaseButton
        >
      </div>
      <div v-else class="ot-ced__bar">
        <v-icon size="15" icon="mdi-file-code-outline" class="ot-ced__mark" />
        <span class="ot-ced__copy" data-zone="saved" title="改了就生效；每次改动自动记进历史，能撤回">{{
          saving ? '正式版 · 正在改…' : '正式版 · 改了就生效'
        }}</span>
        <DocPresence v-if="faces.length" :peers="faces" class="ot-ced__faces" />
        <span class="ot-head__gap" />
        <BaseButton
          v-if="shared"
          id="b-branch"
          kind="ghost"
          size="sm"
          prepend-icon="mdi-source-branch"
          title="在自己的分支上改，改好了再合回正式版"
          @click="openBranch"
          >开分支</BaseButton
        >
        <BaseButton id="b-run" kind="secondary" size="sm" prepend-icon="mdi-play" @click="run">运行</BaseButton>
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

    <div class="ot-ced__panel" :class="{ 'is-open': open }" data-zone="terminal">
      <div class="ot-ced__ptabs">
        <button
          v-for="t in [
            { key: 'out', label: '输出' },
            { key: 'term', label: '终端' },
          ] as const"
          :key="t.key"
          type="button"
          class="ot-ced__ptab"
          :class="{ 'is-on': S.outTab === t.key && open }"
          @click="showTab(t.key)"
        >
          {{ t.label }}
        </button>
        <span class="ot-head__gap" />
        <span class="ot-ced__mine"><v-icon size="13" icon="mdi-lock-outline" />你的环境 · 只有你能看到</span>
        <BaseButton
          kind="ghost"
          size="sm"
          :icon="open ? 'mdi-chevron-down' : 'mdi-chevron-up'"
          :title="open ? '收起' : '展开'"
          :aria-label="open ? '收起' : '展开'"
          @click="open = !open"
        />
      </div>
      <div v-if="open && S.outTab === 'out'" class="ot-ced__pbody" role="status">
        <div v-if="!out" class="ot-ced__hint">点「运行」，在你自己的环境里跑这份代码</div>
        <div v-else-if="out.kind === 'running'" class="ot-ced__cmd">运行中…</div>
        <template v-else>
          <div class="ot-ced__cmd">$ {{ out.cmd }}</div>
          <pre class="ot-ced__res">{{ out.out }}</pre>
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

.ot-ced__bar--branch {
  background: var(--accent-wash);
}

.ot-ced__bar--branch .ot-ced__mark {
  color: var(--accent-ink);
}

.ot-ced__branch {
  font-weight: 600;
  color: var(--ink);
  white-space: nowrap;
}

.ot-ced__ok {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  font-size: 12px;
  color: var(--ok-ink);
  white-space: nowrap;
}

.ot-ced__copy {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-ced__copy code {
  font-family: var(--font-mono);
  font-size: 12px;
}

.ot-ced__bar > :not(.ot-ced__copy, .ot-head__gap) {
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
