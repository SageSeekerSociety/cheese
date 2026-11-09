<script setup lang="ts">
// 项目文件: the one official version. The tree is the product's file tree; a
// document opens in the product's document editor, a Word file shows 芝士's
// 修订 in the product's Word comparison, code is read-only on the official
// version (the product's diff viewer, every line as context), and a result is
// rendered Markdown.
import type { FileRow } from '@/lib/changesTree'
import type { FileDiff } from '@/lib/diff'
import type { OfficeComparison } from '@/types/reviewComment'

import { computed } from 'vue'

import {
  CRITERIA_DOC_HTML,
  FILES,
  MINUTES_DOC,
  minutesHtml,
  preprocessFile,
  RESULTS_MD,
  retrievalFile,
  TASK_DIFFS,
  WORD_COMPARISON,
} from './data'
import DemoDoc from './DemoDoc.vue'
import { S, t4 } from './state'

import MarkdownView from '@/components/common/MarkdownView.vue'
import ChangesDiff from '@/components/panels/ChangesDiff.vue'
import ChangesFileTree from '@/components/panels/ChangesFileTree.vue'
import OfficeCompare from '@/components/review/OfficeCompare.vue'
import { buildFileRows } from '@/lib/changesTree'

const accepted = computed(() => t4()?.status === 'done')

const rows = computed<FileRow[]>(() => {
  const files = FILES.filter((f) => f.kind !== 'result' || accepted.value).map((f) => ({ path: f.path, bytes: 2048 }))
  const diffByPath = new Map<string, FileDiff>()
  // Only the result is new to the official version; the code files are simply its current text.
  const result = TASK_DIFFS.find((d) => d.status === 'added')
  if (accepted.value && result) diffByPath.set(result.path, result)
  return buildFileRows({ files, diffByPath, collapsedDirs: new Set() })
})

const entry = computed(() => FILES.find((f) => f.path === S.file)!)

const minutes = computed(() => {
  const html = minutesHtml(S.suggAccepted)
  return S.typed ? html.replace('<li><p></p></li>', `<li><p>${S.typed}</p></li>`) : html
})

const code = computed(() =>
  S.file === 'eval/preprocess.py' ? preprocessFile(accepted.value) : retrievalFile(accepted.value)
)

function select(path: string) {
  if (FILES.some((f) => f.path === path)) S.file = path
}
</script>

<template>
  <section class="ot-files" data-zone="center">
    <header class="ot-head">
      <h2 class="ot-head__title">项目文件</h2>
      <span class="ot-head__sub">只有一份正式版：文档实时合，代码做完再合</span>
    </header>
    <div class="ot-files__split">
      <div class="ot-files__tree" data-zone="tree">
        <ChangesFileTree
          :rows="rows"
          :collapsed-dirs="new Set()"
          :active-path="S.file"
          :reveal-tick="0"
          empty-label="暂无文件"
          :width="224"
          @select="select"
        />
      </div>
      <div class="ot-files__body">
        <div class="ot-files__bar">
          <span class="ot-files__path">{{ S.file }}</span>
          <span class="ot-head__gap" />
          <span v-if="entry.kind === 'code'" class="ot-files__tag">正式版 · 只读</span>
          <span v-else-if="entry.kind === 'word'" class="ot-files__tag">在线编辑 · 芝士的修订 2 处</span>
          <span v-else-if="entry.kind === 'doc'" class="ot-files__tag">正式版 · 直接改</span>
        </div>
        <div class="ot-files__view">
          <template v-if="S.file === MINUTES_DOC">
            <DemoDoc :html="minutes" peer :typing="S.typed.length > 0" />
            <p class="ot-hint">
              文档直接改：你打的字马上进正式版，王磊也在改。标出来的那段是芝士的建议，还没生效，在右栏接受。
            </p>
          </template>
          <DemoDoc v-else-if="entry.kind === 'doc'" :html="CRITERIA_DOC_HTML" />
          <template v-else-if="entry.kind === 'word'">
            <p class="ot-hint ot-hint--top">
              在线用 Word 编辑器打开。你直接改；芝士的改动以 Word 修订显示，逐条接受或拒绝。
            </p>
            <OfficeCompare :comparison="WORD_COMPARISON as OfficeComparison" />
          </template>
          <template v-else-if="entry.kind === 'code'">
            <div class="ot-files__code"><ChangesDiff :lines="code" inline /></div>
            <p class="ot-hint">
              代码做完验证再合：要改就开一件事，在任务的那份里改，采纳后合进正式版。改个错字这类小事可以直接改。
            </p>
          </template>
          <template v-else>
            <MarkdownView :source="RESULTS_MD" />
            <p class="ot-hint">任务采纳时直接进了项目文件，不用再「保存」。</p>
          </template>
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
  gap: 8px;
  align-items: center;
  height: 36px;
  padding: 0 16px;
  border-bottom: 1px solid var(--line);
}

.ot-files__path {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
}

.ot-files__tag {
  padding: 0 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.ot-files__view {
  flex: 1;
  min-height: 0;
  padding: 16px 24px;
  overflow: auto;
}

.ot-files__code {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.ot-files__code :deep(.diff-line--hunk) {
  display: none;
}

/* The official version has one line number per line: hide the "before" column of the diff gutter. */
.ot-files__code :deep(.diff-line > .diff-line__num:first-of-type) {
  display: none;
}

.ot-hint {
  padding: 8px 12px;
  margin: 16px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-md);
}

.ot-hint--top {
  margin: 0 0 16px;
}
</style>
