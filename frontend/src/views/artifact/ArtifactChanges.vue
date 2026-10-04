<script setup lang="ts">
// 两版之间改了哪些文件：先是一张清单，点开哪一个才看它那一段差异。
//
// 一次合并常常动几十个文件，全部摊开是一整屏红绿，人要找的那一个被埋在里面。清单上
// 每一行给出增删了几行，够人判断先看哪一个。
import type { ArtifactComparison } from '@/api'

import { computed, ref, watch } from 'vue'

import { noteText as note, statusText as status } from './notes'

import { t } from '@/i18n'
import { parseDiffLines } from '@/lib/diff'

const props = defineProps<{ files: ArtifactComparison['files'] }>()

const open = ref<Set<string>>(new Set())
watch(
  () => props.files,
  (files) => {
    // 只改了一个文件时直接摊开：清单上只有一行，还要人点一下没有意义。
    open.value = new Set(files.length === 1 ? [files[0].path] : [])
  },
  { immediate: true }
)

function toggle(path: string) {
  const next = new Set(open.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  open.value = next
}

const rows = computed(() =>
  props.files.map((file) => {
    const lines = file.diff ? parseDiffLines(file.diff) : []
    return {
      file,
      lines,
      added: lines.filter((line) => line.kind === 'add').length,
      removed: lines.filter((line) => line.kind === 'del').length,
    }
  })
)
</script>

<template>
  <ul class="changes">
    <!-- Deliberately not an AppPage: this is a section inside ProjectArtifactView,
         which already owns the page frame (AppPage width="full"). It renders in
         that page's column; a page frame here would nest a second header and
         re-centre the column. -->

    <li v-for="row in rows" :key="row.file.path" class="changes__file">
      <button
        type="button"
        class="changes__head"
        :aria-expanded="open.has(row.file.path)"
        @click="toggle(row.file.path)"
      >
        <v-icon :icon="open.has(row.file.path) ? 'mdi-chevron-down' : 'mdi-chevron-right'" size="16" />
        <span class="changes__path mono">{{ row.file.path }}</span>
        <span v-if="row.file.status" class="t-meta c-faint">{{ status(row.file.status) }}</span>
        <span v-if="row.added" class="changes__add mono">+{{ row.added }}</span>
        <span v-if="row.removed" class="changes__del mono">−{{ row.removed }}</span>
      </button>
      <div v-if="open.has(row.file.path)" class="changes__body">
        <p v-if="row.file.note" class="t-meta c-muted changes__note">{{ note(row.file.note) }}</p>
        <pre
          v-if="row.lines.length"
          class="changes__diff mono"
        ><span v-for="(line, index) in row.lines" :key="index" :class="`diff-${line.kind}`">{{ line.text }}</span></pre>
        <p v-else-if="!row.file.note" class="t-meta c-muted changes__note">{{ t('tasks.artifact.noLineChanges') }}</p>
      </div>
    </li>
  </ul>
</template>

<style scoped>
.mono {
  font-family: var(--font-mono);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.changes {
  margin: 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
}
.changes__file + .changes__file {
  border-top: 1px solid var(--line);
}
.changes__head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 44px;
  padding: 8px 12px;
  border: 0;
  background: none;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}
.changes__head:hover {
  background: var(--fill);
}
.changes__path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.changes__add {
  color: var(--ok-ink);
}
.changes__del {
  color: var(--danger-ink);
}
.changes__body {
  border-top: 1px solid var(--line);
}
.changes__note {
  margin: 0;
  padding: 8px 12px;
}
.changes__diff {
  margin: 0;
  max-height: 480px;
  overflow: auto;
}
.changes__diff span {
  display: block;
  width: max-content;
  min-width: 100%;
  min-height: var(--lh-14);
  padding: 0 12px;
}
.diff-add {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.diff-del {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.diff-hunk,
.diff-meta {
  background: var(--fill);
  color: var(--muted);
}
</style>
