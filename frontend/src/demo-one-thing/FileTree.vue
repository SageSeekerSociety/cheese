<script setup lang="ts">
// 项目文件's tree, drawn the way the product's file library (LibraryRows) draws
// a file: the UI font, a muted type icon from fileIcon, a light grey row for
// the open one, as the sidebar marks the open channel. The product's
// ChangesFileTree is the diff-review list (monospace, one icon for every file,
// accent selection), which reads as a code review here, not as a project's files.
// The rows themselves still come from the product's lib/changesTree.
import type { FileRow } from '@/lib/changesTree'

import { fileIcon } from '@/lib/fileKind'

defineProps<{ rows: FileRow[]; collapsedDirs: Set<string>; activePath: string | null }>()
const emit = defineEmits<{ (e: 'select', path: string): void; (e: 'toggle-dir', path: string): void }>()
</script>

<template>
  <div class="ot-ftree" role="tree" aria-label="项目文件">
    <template v-for="row in rows" :key="`${row.type}:${row.path}`">
      <button
        v-if="row.type === 'dir'"
        type="button"
        role="treeitem"
        class="ot-ftree__row ot-ftree__row--dir"
        :aria-expanded="!collapsedDirs.has(row.path)"
        aria-selected="false"
        :style="{ paddingLeft: `${6 + row.depth * 16}px` }"
        :title="row.path"
        @click="emit('toggle-dir', row.path)"
      >
        <v-icon
          size="16"
          class="ot-ftree__chev"
          :icon="collapsedDirs.has(row.path) ? 'mdi-chevron-right' : 'mdi-chevron-down'"
        />
        <v-icon
          size="16"
          class="ot-ftree__icon"
          :icon="collapsedDirs.has(row.path) ? 'mdi-folder-outline' : 'mdi-folder-open-outline'"
        />
        <span class="ot-ftree__name">{{ row.name }}</span>
      </button>
      <button
        v-else
        type="button"
        role="treeitem"
        class="ot-ftree__row"
        :class="{ 'is-active': activePath === row.path }"
        :aria-selected="activePath === row.path"
        :style="{ paddingLeft: `${6 + row.depth * 16 + 18}px` }"
        :title="row.path"
        @click="emit('select', row.path)"
      >
        <v-icon size="16" class="ot-ftree__icon" :icon="fileIcon(row.path)" />
        <span class="ot-ftree__name">{{ row.name }}</span>
        <span v-if="row.diff?.status === 'added'" class="ot-ftree__mark">新</span>
      </button>
    </template>
  </div>
</template>

<style scoped>
.ot-ftree {
  display: flex;
  flex-direction: column;
  gap: 1px;
  width: 100%;
  padding: 8px;
  overflow-y: auto;
}

.ot-ftree__row {
  display: flex;
  flex: none;
  gap: 6px;
  align-items: center;
  width: 100%;
  height: 30px;
  padding-right: 8px;
  font: inherit;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.ot-ftree__row:hover {
  background: var(--fill);
}

/* The open file: the sidebar's selected row (grey fill, ink, a step bolder), no accent. */
.ot-ftree__row.is-active {
  font-weight: 600;
  color: var(--ink);
  background: var(--line-2);
}

.ot-ftree__row--dir {
  gap: 2px;
  color: var(--ink);
}

.ot-ftree__row--dir .ot-ftree__icon {
  margin-right: 4px;
}

.ot-ftree__chev,
.ot-ftree__icon {
  flex: none;
  color: var(--muted);
}

.ot-ftree__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-ftree__mark {
  flex: none;
  padding: 0 6px;
  font-size: 11px;
  font-weight: 400;
  line-height: 18px;
  color: var(--ok-ink);
  background: var(--ok-wash);
  border-radius: var(--radius-sm);
}
</style>
