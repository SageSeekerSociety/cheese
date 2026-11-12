<script setup lang="ts">
// A code file in 项目文件: 正式版, read-only, in every project. Code is never
// typed into here: every change to it is a 一件事, whoever does it. The bar
// has one action, 「改这个文件」: it opens a 一件事 I do, in the file's channel
// like any other, straight into its 工作台 with this file open
// (state.editFile). When a 一件事 I am doing already has this file (in a
// project claimed from a 题目, the one 领取 opened) the button opens that one
// instead and says so: 「去「做 Data Lab」里改」. While any 一件事 changes the
// file the bar says so, 「有 1 件事在改这个文件 ›」, which opens it on its
// 工作台; the right panel lists them (FilesPanel). A file mention clicked in a
// channel brings its lines into view with a band. A submission opened from
// its 题目's 提交记录 shows here read-only, pinned to its version.
// The surface is DemoCodeArea, read-only (why it is not Monaco is said there).
import { computed } from 'vue'

import { asContext, isClaim } from './data'
import DemoCodeArea from './DemoCodeArea.vue'
import { canWork, editFile, officialText, openBench, resultOf, S, thingsOnFile } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import ChangesDiff from '@/components/panels/ChangesDiff.vue'

const p = computed(() => S.project)
const path = computed(() => S.file)
const text = computed(() => officialText(p.value, path.value))
const mark = computed(() => (S.codeAt?.path === path.value ? S.codeAt : null))
/** The 一件事 changing this file now. */
const on = computed(() => thingsOnFile(p.value, path.value))
/** One I am doing already: 「改这个文件」 goes there instead of opening a second. */
const mine = computed(() => on.value.find((t) => canWork(t)) ?? null)

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
          >回到正式版</BaseButton
        >
      </div>
      <div class="ot-ced__pinned"><ChangesDiff :lines="pinned" inline /></div>
    </template>

    <template v-else>
      <div class="ot-ced__bar">
        <v-icon size="15" icon="mdi-lock-outline" class="ot-ced__mark" />
        <span class="ot-ced__copy" data-zone="official" title="代码在这里只读：改它就开一件事，采纳了才进正式版"
          >正式版 · 只读</span
        >
        <button
          v-if="on.length && !mine"
          type="button"
          class="ot-ced__things"
          data-things-on-file
          title="打开那件事的工作台"
          @click="openBench(on[0]!.id, path)"
        >
          有 {{ on.length }} 件事在改这个文件 ›
        </button>
        <span class="ot-head__gap" />
        <BaseButton
          id="b-edit-file"
          kind="primary"
          size="sm"
          prepend-icon="mdi-pencil-outline"
          :title="mine ? `在「${mine.title}」的工作台里接着改` : '开一件事来改：在它的工作台里改、运行，改好了采纳'"
          @click="editFile(p, path)"
          >{{ mine ? `去「${mine.title}」里改` : '改这个文件' }}</BaseButton
        >
      </div>
      <div class="ot-ced__edit">
        <DemoCodeArea :model-value="text" :label="`${path}（正式版，只读）`" readonly :mark="mark" />
      </div>
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

/* A 一件事 changing this file: a quiet note, not an action; it opens the 一件事. */
.ot-ced__things {
  flex: 0 1 auto;
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
</style>
