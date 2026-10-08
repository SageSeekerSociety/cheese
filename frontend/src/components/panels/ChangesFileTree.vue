<script setup lang="ts">
// 「改动」那一格左边的文件树：一排排已经折好的行，点文件通知外面，点文件夹就地开合。
//
// 它自己不取数、也不知道哪些文件被改过：行是外面算好递进来的（`lib/changesTree.ts`），
// 每一行上的增删标记是行自己带的。唯一它自己管的是「哪一行要滚进视野」——那是画的
// 事情，而它才知道自己那些行画在哪。
import type { FileRow } from '../../lib/changesTree'

import { nextTick, ref, watch } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  rows: FileRow[]
  /** 收起的文件夹；其余一律摊开。 */
  collapsedDirs: Set<string>
  activePath: string | null
  /** 外面点了一枚 <&path> 芯片：把这一行滚进视野（每一跳都加一）。 */
  revealTick: number
  /** 空的时候说哪句话。 */
  emptyLabel: string
  /** 这一列的宽度（px），由外面那条分隔线拖出来。 */
  width?: number
}>()

const emit = defineEmits<{
  (e: 'select', path: string): void
  (e: 'toggle-dir', path: string): void
}>()

const listEl = ref<HTMLElement | null>(null)

// 定位: the file was opened from somewhere else (a <path> chip in chat or the doc),
// so bring its row into view. The ancestor folders are already open by then — the
// data layer opened them — this only scrolls.
watch(
  () => props.revealTick,
  () =>
    void nextTick(() => {
      listEl.value?.querySelector('.file-item--active')?.scrollIntoView({ block: 'nearest' })
    })
)
</script>

<template>
  <div ref="listEl" class="file-list" :style="props.width ? { flexBasis: `${props.width}px` } : undefined">
    <div v-if="props.rows.length === 0" class="text-center c-faint py-6 t-body">{{ props.emptyLabel }}</div>
    <template v-for="row in props.rows" :key="`${row.type}:${row.path}`">
      <!-- folder row: click toggles expand/collapse -->
      <button
        v-if="row.type === 'dir'"
        type="button"
        class="file-item file-item--dir"
        :style="{ paddingLeft: `${8 + row.depth * 14}px` }"
        :title="row.path"
        @click="emit('toggle-dir', row.path)"
      >
        <v-icon size="13" class="c-muted">
          {{ props.collapsedDirs.has(row.path) ? 'mdi-chevron-right' : 'mdi-chevron-down' }}
        </v-icon>
        <v-icon size="13" class="me-1 c-muted">
          {{ props.collapsedDirs.has(row.path) ? 'mdi-folder-outline' : 'mdi-folder-open-outline' }}
        </v-icon>
        <span class="file-item__name">{{ row.name }}</span>
      </button>
      <!-- file row: name, and how much this topic changed in it -->
      <button
        v-else
        type="button"
        class="file-item"
        :class="{ 'file-item--active': props.activePath === row.path }"
        :style="{ paddingLeft: `${8 + row.depth * 14 + 13}px` }"
        :title="`${row.path} · ${row.size}`"
        @click="emit('select', row.path)"
      >
        <v-icon size="13" class="me-1 c-muted">mdi-file-outline</v-icon>
        <span class="file-item__name">{{ row.name }}</span>
        <!-- 变更标记: 新增 / 删除 说的是这个文件本身的去留，改过的给增删行数。 -->
        <span v-if="row.diff?.status === 'added'" class="file-mark file-mark--add">{{
          t('work.room.changes.fileAdded')
        }}</span>
        <span v-else-if="row.diff?.status === 'removed'" class="file-mark file-mark--del">{{
          t('work.room.changes.fileRemoved')
        }}</span>
        <template v-else-if="row.diff">
          <span v-if="row.diff.added" class="file-mark file-mark--add">+{{ row.diff.added }}</span>
          <span v-if="row.diff.removed" class="file-mark file-mark--del">−{{ row.diff.removed }}</span>
        </template>
      </button>
    </template>
  </div>
</template>

<style scoped>
.file-list {
  flex: 0 0 220px;
  overflow-y: auto;
  background: var(--fill);
  border-right: 1px solid rgba(var(--v-border-color), 0.5);
  padding: 4px 0;
}
.file-item {
  display: flex;
  align-items: center;
  width: 100%;
  text-align: left;
  padding: 3px 8px 3px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--text);
}
.file-item--dir .file-item__name {
  font-weight: 500;
}
.file-item:hover {
  background: rgba(var(--v-border-color), 0.18);
}
.file-item--active {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
}
.file-item__name {
  font-family: var(--font-mono);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-item--active :deep(.v-icon) {
  color: rgb(var(--v-theme-primary));
}
</style>
