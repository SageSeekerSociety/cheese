<script setup lang="ts">
// A project's code view, in every project: the code, 运行, and a bottom panel
// bound to my own environment (输出 and 终端, only I can see it; it is my
// terminal, not 芝士's). In the research project the file reads as its
// official version until 改代码, which edits a copy that 合进正式版 puts in
// (请人采纳 while 代码受保护 is on). In a project claimed from the course the
// editor is the file itself: 保存 (⌘S) changes it and 历史 records each save.
// 提交评测 shows only on the file of a judged unit and pins the version it
// judged; with a submission open, the view shows that version read-only.
// The editing surface is DemoCodeArea (why it is not Monaco is said there).
import { computed, nextTick, ref, watch } from 'vue'

import { asContext } from './data'
import DemoCodeArea from './DemoCodeArea.vue'
import JudgeResult from './JudgeResult.vue'
import {
  changedLines,
  draftText,
  fkey,
  isUnsaved,
  mergeCode,
  officialCode,
  runCode,
  S,
  saveCode,
  submitCode,
  termRun,
  typeCode,
  unitOf,
} from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import ChangesDiff from '@/components/panels/ChangesDiff.vue'

const p = computed(() => S.project)
const path = computed(() => S.file)
const k = computed(() => fkey(p.value, path.value))
const research = computed(() => p.value === 'research')
/** Research: the copy opened by 改代码 is on this file. */
const editing = computed(() => S.codeDraft?.path === path.value)
const text = computed({
  get: () => draftText(p.value, path.value),
  set: (v: string) => typeCode(p.value, path.value, v),
})
const unsaved = computed(() => isUnsaved(p.value, path.value))
const unit = computed(() => unitOf(p.value, path.value))
const changed = computed(() => (editing.value ? changedLines(path.value, text.value.split('\n')) : 0))
const official = computed(() => asContext(path.value, officialCode(path.value)))

// ---- a pinned version, read-only ----
const sub = computed(() =>
  S.viewSub && (p.value === 'datalab' || p.value === 'week3')
    ? S.subs[p.value].find((x) => x.id === S.viewSub) ?? null
    : null
)
const pinned = computed(() => (sub.value ? asContext(path.value, sub.value.code.replace(/\n$/, '').split('\n')) : []))

// ---- the bottom panel ----
const open = ref(true)
const out = computed(() => S.out[k.value])
const outSub = computed(() => {
  const o = out.value
  if (o?.kind !== 'judge' || (p.value !== 'datalab' && p.value !== 'week3')) return null
  return S.subs[p.value].find((x) => x.id === o.sub) ?? null
})
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

const save = () => saveCode(p.value, path.value)
function run() {
  open.value = true
  runCode(p.value, path.value)
}
function submit() {
  open.value = true
  submitCode(p.value, path.value)
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
          >第 {{ sub.n }} 次提交评测的版本 <code>{{ sub.version }}</code> · {{ sub.score }}/{{ sub.total }} · 只读</span
        >
        <span class="ot-head__gap" />
        <BaseButton kind="secondary" size="sm" prepend-icon="mdi-arrow-left" @click="S.viewSub = null"
          >回到正在改的</BaseButton
        >
      </div>
      <div class="ot-ced__pinned"><ChangesDiff :lines="pinned" inline /></div>
    </template>

    <template v-else-if="research && !editing">
      <div class="ot-ced__bar">
        <v-icon size="15" icon="mdi-file-check-outline" class="ot-ced__mark" />
        <span class="ot-ced__copy">正式版 · 改代码在你自己的副本上改</span>
        <span class="ot-head__gap" />
        <BaseButton id="b-run" kind="secondary" size="sm" prepend-icon="mdi-play" @click="run">运行</BaseButton>
      </div>
      <div class="ot-ced__pinned"><ChangesDiff :lines="official" inline /></div>
    </template>

    <template v-else-if="research">
      <div class="ot-ced__bar">
        <v-icon size="15" icon="mdi-source-branch" class="ot-ced__mark" />
        <span class="ot-ced__copy">你的副本 · 基于正式版</span>
        <span class="ot-head__gap" />
        <BaseButton kind="ghost" size="sm" @click="S.codeDraft = null">放弃</BaseButton>
        <BaseButton id="b-run" kind="secondary" size="sm" prepend-icon="mdi-play" @click="run">运行</BaseButton>
        <BaseButton id="b-merge-code" kind="primary" size="sm" :disabled="!changed" @click="mergeCode">
          {{ S.settingsProtectCode ? '请人采纳' : '合进正式版' }}
        </BaseButton>
      </div>
      <div class="ot-ced__edit"><DemoCodeArea v-model="S.codeDraft!.text" :label="`编辑 ${path}`" /></div>
    </template>

    <template v-else>
      <div class="ot-ced__bar">
        <v-icon size="15" icon="mdi-file-code-outline" class="ot-ced__mark" />
        <span class="ot-ced__copy">{{ unsaved ? '有改动没保存' : '已保存' }}</span>
        <span class="ot-head__gap" />
        <BaseButton id="b-run" kind="secondary" size="sm" prepend-icon="mdi-play" @click="run">运行</BaseButton>
        <BaseButton
          id="b-save"
          :kind="unit ? 'secondary' : 'primary'"
          size="sm"
          :disabled="!unsaved"
          title="保存（⌘S）"
          @click="save"
          >保存</BaseButton
        >
        <BaseButton
          v-if="unit"
          id="b-submit"
          kind="primary"
          size="sm"
          prepend-icon="mdi-send-check-outline"
          :loading="S.judging === k"
          @click="submit"
          >提交评测</BaseButton
        >
      </div>
      <div class="ot-ced__edit">
        <DemoCodeArea v-model="text" :label="`编辑 ${path}`" @save="save" />
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
        <div v-if="!out" class="ot-ced__hint">
          点「运行」，在你自己的环境里跑这份代码{{ unit ? '；「提交评测」交给评测机' : '' }}
        </div>
        <div v-else-if="out.kind === 'running'" class="ot-ced__cmd">运行中…</div>
        <template v-else-if="out.kind === 'run'">
          <div class="ot-ced__cmd">$ {{ out.cmd }}</div>
          <pre class="ot-ced__res">{{ out.out }}</pre>
        </template>
        <div v-else-if="out.kind === 'judging'" class="ot-ced__cmd">
          评测中… 版本 {{ out.version }}：编译、跑测试{{ p === 'datalab' ? '、dlc 检查' : '' }}
        </div>
        <template v-else-if="outSub">
          <div class="ot-ced__cmd">第 {{ outSub.n }} 次提交 · 版本 {{ outSub.version }}</div>
          <JudgeResult :sub="outSub" />
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
