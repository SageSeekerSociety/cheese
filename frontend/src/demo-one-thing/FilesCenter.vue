<script setup lang="ts">
// 项目文件: the one official version. The tree reads like the product's file
// library; a document opens in the product's document editor, a Word file shows
// 芝士's 修订 in the product's Word comparison, code shows the official version
// (the product's diff viewer, every line as context) and 改代码 edits it on the
// person's own copy, and a result is rendered Markdown. docs/频道/ holds each
// channel's 文档: the same document the channel shows, live block and all.
import type { FileRow } from '@/lib/changesTree'
import type { FileDiff } from '@/lib/diff'
import type { OfficeComparison } from '@/types/reviewComment'

import { computed, ref, watch } from 'vue'

import ChannelDoc from './ChannelDoc.vue'
import { asContext, DOC_HTML, FILES, MINUTES_DOC, minutesHtml, RESULTS_MD, TASK_DIFFS, WORD_COMPARISON } from './data'
import DemoCodeEdit from './DemoCodeEdit.vue'
import DemoDoc from './DemoDoc.vue'
import FileTree from './FileTree.vue'
import { editCode, officialCode, S, t4, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import ChangesDiff from '@/components/panels/ChangesDiff.vue'
import OfficeCompare from '@/components/review/OfficeCompare.vue'
import { buildFileRows } from '@/lib/changesTree'
import { fileIcon } from '@/lib/fileKind'

const accepted = computed(() => t4()?.status === 'done')
const collapsed = ref(new Set<string>())

const shown = computed(() => FILES.filter((f) => f.kind !== 'result' || accepted.value))
const rows = computed<FileRow[]>(() => {
  const diffByPath = new Map<string, FileDiff>()
  // Only the result is new to the official version; the code files are simply its current text.
  const result = TASK_DIFFS.find((d) => d.status === 'added')
  if (accepted.value && result) diffByPath.set(result.path, result)
  return buildFileRows({
    files: shown.value.map((f) => ({ path: f.path, bytes: f.bytes })),
    diffByPath,
    collapsedDirs: collapsed.value,
  })
})

const entry = computed(() => FILES.find((f) => f.path === S.file) ?? FILES[1]!)

const minutes = computed(() => {
  const html = S.suggRejected
    ? minutesHtml(false)
        .replace(/<ins[^>]*>.*?<\/ins>/, '')
        .replace(/<del[^>]*>(.*?)<\/del>/, '$1')
    : minutesHtml(S.suggAccepted)
  return S.typed ? html.replace('<li><p></p></li>', `<li><p>${S.typed}</p></li>`) : html
})

const code = computed(() => asContext(entry.value.path, officialCode(entry.value.path)))
const editing = computed(() => S.codeDraft?.path === entry.value.path)
// A copy belongs to the file it was opened on; moving to another file drops it.
watch(
  () => S.file,
  () => (S.codeDraft = null)
)

function select(path: string) {
  if (FILES.some((f) => f.path === path)) S.file = path
}
function toggleDir(path: string) {
  const next = new Set(collapsed.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  collapsed.value = next
}
const crumbs = computed(() => entry.value.path.split('/'))
const sizeOf = (b: number) => (b > 1024 ? `${(b / 1024).toFixed(1)} KB` : `${b} B`)
</script>

<template>
  <section class="ot-files" data-zone="center">
    <header class="ot-head">
      <h2 class="ot-head__title">项目文件</h2>
      <span class="ot-head__sub">{{ shown.length }} 个文件</span>
      <span class="ot-head__gap" />
      <BaseButton kind="ghost" size="sm" prepend-icon="mdi-plus" @click="toast('新建：文档、表格或文件夹')"
        >新建</BaseButton
      >
      <BaseButton kind="ghost" size="sm" prepend-icon="mdi-upload-outline" @click="toast('上传：拖进来或从电脑里选')"
        >上传</BaseButton
      >
    </header>
    <div class="ot-files__split">
      <div class="ot-files__tree" data-zone="tree">
        <FileTree
          :rows="rows"
          :collapsed-dirs="collapsed"
          :active-path="S.file"
          @select="select"
          @toggle-dir="toggleDir"
        />
      </div>
      <div class="ot-files__body">
        <div class="ot-files__bar">
          <v-icon size="16" :icon="fileIcon(entry.path)" class="ot-files__icon" />
          <span class="ot-files__path">
            <template v-for="(c, i) in crumbs" :key="i">
              <span v-if="i" class="ot-files__slash">/</span>
              <span :class="{ 'ot-files__leaf': i === crumbs.length - 1 }">{{ c }}</span>
            </template>
          </span>
          <span class="ot-files__size">{{ sizeOf(entry.bytes) }}</span>
          <span class="ot-head__gap" />
          <span v-if="entry.kind === 'code' && S.codePending[entry.path]" class="ot-files__tag">你的改动等人采纳</span>
          <BaseButton
            v-if="entry.kind === 'code' && !editing"
            id="b-edit-code"
            kind="secondary"
            size="sm"
            prepend-icon="mdi-pencil-outline"
            @click="editCode(entry.path)"
            >改代码</BaseButton
          >
          <span v-else-if="entry.kind === 'word'" class="ot-files__tag">修订 2 处</span>
          <BaseButton
            kind="ghost"
            size="sm"
            icon="mdi-download-outline"
            title="下载"
            aria-label="下载"
            @click="toast(`下载 ${entry.path.split('/').pop()}`)"
          />
        </div>
        <div class="ot-files__view" :class="{ 'ot-files__view--flush': entry.kind === 'code' || entry.chan }">
          <DemoDoc v-if="entry.path === MINUTES_DOC" :html="minutes" peer :typing="S.typed.length > 0" />
          <ChannelDoc v-else-if="entry.chan" :key="entry.chan" :chan="entry.chan" />
          <DemoDoc v-else-if="entry.kind === 'doc'" :key="entry.path" :html="DOC_HTML[entry.path] ?? ''" />
          <OfficeCompare v-else-if="entry.kind === 'word'" :comparison="WORD_COMPARISON as OfficeComparison" />
          <DemoCodeEdit v-else-if="editing" />
          <div v-else-if="entry.kind === 'code'" class="ot-files__code"><ChangesDiff :lines="code" inline /></div>
          <MarkdownView v-else :source="RESULTS_MD" />
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ot-files {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.ot-files__split {
  display: flex;
  flex: 1;
  min-height: 0;
}

.ot-files__tree {
  display: flex;
  flex: none;
  width: 224px;
  min-height: 0;
  border-right: 1px solid var(--line);
}

.ot-files__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.ot-files__bar {
  display: flex;
  flex: none;
  gap: 8px;
  align-items: center;
  height: 40px;
  padding: 0 8px 0 16px;
  border-bottom: 1px solid var(--line);
}

.ot-files__icon {
  color: var(--muted);
}

.ot-files__path {
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-files__slash {
  margin: 0 4px;
  color: var(--faint);
}

.ot-files__leaf {
  font-weight: 600;
  color: var(--ink);
}

.ot-files__size {
  font-size: 12px;
  color: var(--faint);
  white-space: nowrap;
}

.ot-files__tag {
  padding: 0 8px;
  font-size: 12px;
  line-height: 20px;
  color: var(--muted);
  white-space: nowrap;
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.ot-files__view {
  flex: 1;
  min-height: 0;
  padding: 16px 32px;
  overflow: auto;
}

.ot-files__view--flush {
  padding: 0;
}

.ot-files__code :deep(.diff-line--hunk) {
  display: none;
}

/* The official version has one line number per line: hide the "before" column of the diff gutter. */
.ot-files__code :deep(.diff-line > .diff-line__num:first-of-type) {
  display: none;
}

.ot-files__code :deep(.diff-line__text) {
  white-space: pre;
}
</style>
