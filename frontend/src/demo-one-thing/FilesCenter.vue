<script setup lang="ts">
// 项目文件: the project's files. The tree reads like the product's file
// library; a document opens in the product's document editor, a Word file shows
// 芝士's 修订 in the product's Word comparison, a code file opens in the code
// view (DemoCodeEdit: the code, 运行 and my own terminal), and a result is
// rendered Markdown. A code file opens editable on 正式版, like a document.
// docs/频道/ holds each channel's 文档. In a project claimed
// from the course, what the 题目 brought in: the 题面 (or the 题单 document)
// pinned on top, and the starter code, folded by problem.
import type { FileRow } from '@/lib/changesTree'
import type { FileDiff } from '@/lib/diff'
import type { OfficeComparison } from '@/types/reviewComment'

import { computed } from 'vue'

import ChannelDoc from './ChannelDoc.vue'
import { PROJECT_FILES } from './course'
import { DOC_HTML, FILES, isClaim, MINUTES_DOC, minutesHtml, RESULTS_MD, TASK_DIFFS, WORD_COMPARISON } from './data'
import DemoCodeEdit from './DemoCodeEdit.vue'
import DemoDoc from './DemoDoc.vue'
import FileTree from './FileTree.vue'
import ProblemSetDoc from './ProblemSetDoc.vue'
import { fkey, isSaving, onBranch, openFile, S, t4, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import OfficeCompare from '@/components/review/OfficeCompare.vue'
import { buildFileRows } from '@/lib/changesTree'
import { fileIcon } from '@/lib/fileKind'

interface Entry {
  path: string
  kind: string
  bytes: number
  chan?: string
  pinned?: boolean
  html?: string
}

const accepted = computed(() => t4()?.status === 'done')
const collapsed = computed(() => new Set(S.collapsed[S.project] ?? []))

const all = computed<Entry[]>(() =>
  isClaim(S.project) ? PROJECT_FILES[S.project] : FILES.filter((f) => f.kind !== 'result' || accepted.value)
)
const pinnedFiles = computed(() => all.value.filter((f) => f.pinned))
const rows = computed<FileRow[]>(() => {
  const diffByPath = new Map<string, FileDiff>()
  // Only the result is new to the official version; the code files are simply its current text.
  const result = TASK_DIFFS.find((d) => d.status === 'added')
  if (S.project === 'research' && accepted.value && result) diffByPath.set(result.path, result)
  return buildFileRows({
    files: all.value.filter((f) => !f.pinned).map((f) => ({ path: f.path, bytes: f.bytes })),
    diffByPath,
    collapsedDirs: collapsed.value,
  })
})

const entry = computed(() => all.value.find((f) => f.path === S.file) ?? all.value[0]!)

const minutes = computed(() => {
  const html = S.suggRejected
    ? minutesHtml(false)
        .replace(/<ins[^>]*>.*?<\/ins>/, '')
        .replace(/<del[^>]*>(.*?)<\/del>/, '$1')
    : minutesHtml(S.suggAccepted)
  return S.typed ? html.replace('<li><p></p></li>', `<li><p>${S.typed}</p></li>`) : html
})

function select(path: string) {
  if (all.value.some((f) => f.path === path)) openFile(path, { ftab: S.ftab })
}
function toggleDir(path: string) {
  const list = (S.collapsed[S.project] ??= [])
  const i = list.indexOf(path)
  if (i >= 0) list.splice(i, 1)
  else list.push(path)
}
const crumbs = computed(() => entry.value.path.split('/'))
const sizeOf = (b: number) => (b > 1024 ? `${(b / 1024).toFixed(1)} KB` : `${b} B`)
</script>

<template>
  <section class="ot-files" data-zone="center">
    <header class="ot-head">
      <h2 class="ot-head__title">项目文件</h2>
      <span class="ot-head__sub">{{ all.length }} 个文件</span>
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
        <div v-if="pinnedFiles.length" class="ot-files__pins">
          <span class="t-eyebrow-read ot-files__pinlabel">置顶</span>
          <button
            v-for="f in pinnedFiles"
            :key="f.path"
            type="button"
            class="ot-files__pin"
            :class="{ 'is-active': S.file === f.path }"
            :title="f.path"
            @click="select(f.path)"
          >
            <v-icon size="16" :icon="f.kind === 'list' ? 'mdi-format-list-checks' : 'mdi-text-box-outline'" />
            <span>{{ f.kind === 'list' ? '题单' : '题面' }}</span>
            <span class="ot-files__pinpath">{{ f.path }}</span>
          </button>
        </div>
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
          <span v-if="entry.pinned" class="ot-files__tag">置顶</span>
          <span v-if="entry.kind === 'code' && onBranch(S.project)" class="ot-files__tag"
            >分支：{{ S.branch!.name }}</span
          >
          <span v-else-if="entry.kind === 'code' && isSaving(S.project, entry.path)" class="ot-files__tag">正在改</span>
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
        <div
          class="ot-files__view"
          :class="{ 'ot-files__view--flush': entry.kind === 'code' || entry.kind === 'list' || entry.chan }"
        >
          <DemoDoc v-if="entry.path === MINUTES_DOC" :html="minutes" peer :typing="S.typed.length > 0" />
          <ChannelDoc v-else-if="entry.chan" :key="entry.chan" :chan="entry.chan" />
          <ProblemSetDoc v-else-if="entry.kind === 'list'" />
          <DemoDoc
            v-else-if="entry.kind === 'doc'"
            :key="fkey(S.project, entry.path)"
            :html="entry.html ?? DOC_HTML[entry.path] ?? ''"
          />
          <OfficeCompare v-else-if="entry.kind === 'word'" :comparison="WORD_COMPARISON as OfficeComparison" />
          <DemoCodeEdit v-else-if="entry.kind === 'code'" :key="fkey(S.project, entry.path)" />
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
  flex-direction: column;
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

.ot-files__tree > :deep(.ot-ftree) {
  flex: 1;
  min-height: 0;
}

.ot-files__pins {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 1px;
  padding: 8px 8px 6px;
  border-bottom: 1px solid var(--line);
}

.ot-files__pinlabel {
  padding: 0 6px 2px;
}

/* A pinned document: the tree's row, with its role in the project as the name. */
.ot-files__pin {
  display: flex;
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

.ot-files__pin:hover {
  background: var(--fill);
}

.ot-files__pin.is-active {
  font-weight: 600;
  color: var(--ink);
  background: var(--line-2);
}

.ot-files__pin .v-icon {
  color: var(--muted);
}

.ot-files__pinpath {
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  font-weight: 400;
  color: var(--faint);
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
