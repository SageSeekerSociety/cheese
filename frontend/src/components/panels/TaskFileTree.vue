<script setup lang="ts">
// The tree of a task's「文件」panel: its rows (`lib/taskFileTree`), a dot on
// each file this task changed, and the person's ways to change the tree —
// new file and new folder at the top, rename and delete on each row (its ⋯
// or a right click). It only says what was asked; the panel does it.
import type { TaskFileRow } from '../../lib/taskFileTree'
import type { MenuAction } from '../common/menuAction'

import { nextTick, ref, watch } from 'vue'

import AdaptiveMenu from '../common/AdaptiveMenu.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  rows: TaskFileRow[]
  open: ReadonlySet<string>
  activePath: string | null
  /** Files this task changed. */
  changed: ReadonlySet<string>
  /** The person may change the tree. */
  editable: boolean
  /** A file can be opened in a cell of its own beside this one. */
  canSplit?: boolean
  /** Bumped when a file opened from elsewhere should be scrolled into view. */
  revealTick: number
}>()

const emit = defineEmits<{
  select: [path: string]
  toggle: [path: string]
  create: [kind: 'file' | 'dir', folder: string]
  rename: [path: string]
  remove: [path: string]
  'open-beside': [path: string]
}>()

const listEl = ref<HTMLElement | null>(null)
watch(
  () => props.revealTick,
  () => void nextTick(() => listEl.value?.querySelector('.tree-row--active')?.scrollIntoView({ block: 'nearest' }))
)

const menuFor = ref<TaskFileRow | null>(null)
const menuOpen = ref(false)
const menuPoint = ref<[number, number] | null>(null)
function actionsOf(row: TaskFileRow): MenuAction[] {
  const beside: MenuAction[] =
    row.type === 'file' && props.canSplit
      ? [
          {
            key: 'beside',
            label: t('work.room.taskFiles.openBeside'),
            icon: 'mdi-dock-right',
            onSelect: () => emit('open-beside', row.path),
          },
        ]
      : []
  if (!props.editable) return beside
  const own: MenuAction[] = [
    {
      key: 'rename',
      label: t('project.artifacts.rename'),
      icon: 'mdi-pencil-outline',
      onSelect: () => emit('rename', row.path),
    },
    {
      key: 'delete',
      label: t('project.artifacts.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      divider: true,
      onSelect: () => emit('remove', row.path),
    },
  ]
  if (row.type === 'file') return [...beside, ...own]
  return [
    {
      key: 'file',
      label: t('work.room.taskFiles.newFile'),
      icon: 'mdi-file-plus-outline',
      onSelect: () => emit('create', 'file', row.path),
    },
    {
      key: 'dir',
      label: t('work.room.taskFiles.newFolder'),
      icon: 'mdi-folder-plus-outline',
      onSelect: () => emit('create', 'dir', row.path),
    },
    ...own,
  ]
}
function showMenu(row: TaskFileRow, event?: MouseEvent) {
  if (!actionsOf(row).length) return
  menuFor.value = row
  menuPoint.value = event ? [event.clientX, event.clientY] : null
  menuOpen.value = true
}
function pick(row: TaskFileRow) {
  if (row.type === 'dir') emit('toggle', row.path)
  else emit('select', row.path)
}
</script>

<template>
  <div class="task-tree">
    <div v-if="props.editable" class="task-tree__bar">
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-file-plus-outline"
        :aria-label="t('work.room.taskFiles.newFile')"
        :title="t('work.room.taskFiles.newFile')"
        @click="emit('create', 'file', '')"
      />
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-folder-plus-outline"
        :aria-label="t('work.room.taskFiles.newFolder')"
        :title="t('work.room.taskFiles.newFolder')"
        @click="emit('create', 'dir', '')"
      />
    </div>
    <div ref="listEl" class="task-tree__list">
      <p v-if="!props.rows.length" class="task-tree__empty">{{ t('work.room.changes.noFiles') }}</p>
      <div
        v-for="row in props.rows"
        :key="`${row.type}:${row.path}`"
        class="tree-row"
        :class="{ 'tree-row--active': row.type === 'file' && row.path === props.activePath }"
      >
        <button
          type="button"
          class="tree-row__main"
          :aria-expanded="row.type === 'dir' ? props.open.has(row.path) : undefined"
          :aria-current="row.type === 'file' && row.path === props.activePath ? 'true' : undefined"
          :style="{ paddingLeft: `${8 + row.depth * 14}px` }"
          :title="row.path"
          @click="pick(row)"
          @contextmenu.prevent="showMenu(row, $event)"
        >
          <v-icon v-if="row.type === 'dir'" size="14" class="tree-row__icon">
            {{ props.open.has(row.path) ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
          </v-icon>
          <v-icon v-else size="14" class="tree-row__icon">mdi-file-outline</v-icon>
          <span class="tree-row__name">{{ row.name }}</span>
          <span
            v-if="row.type === 'file' && props.changed.has(row.path)"
            class="tree-row__dot"
            :aria-label="t('work.room.taskFiles.changed')"
          />
        </button>
        <BaseButton
          v-if="props.editable || (props.canSplit && row.type === 'file')"
          kind="ghost"
          size="sm"
          icon="mdi-dots-horizontal"
          class="tree-row__more"
          :aria-label="t('project.artifacts.actionsOf', { name: row.name })"
          :title="t('project.artifacts.actionsOf', { name: row.name })"
          @click="showMenu(row)"
        />
      </div>
    </div>
    <AdaptiveMenu
      v-if="menuFor"
      v-model="menuOpen"
      :actions="actionsOf(menuFor)"
      :point="menuPoint"
      :title="menuFor.name"
    >
      <template #activator="{ props: activator }">
        <span v-bind="activator" class="task-tree__anchor" />
      </template>
    </AdaptiveMenu>
  </div>
</template>

<style scoped>
.task-tree {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--canvas);
  border-right: 1px solid var(--line);
}
.task-tree__bar {
  display: flex;
  justify-content: flex-end;
  gap: 2px;
  padding: 4px 4px 0;
  flex: none;
}
.task-tree__list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0 8px;
}
.task-tree__empty {
  margin: 0;
  padding: 8px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.tree-row {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 26px;
}
.tree-row:hover {
  background: rgba(var(--v-border-color), 0.18);
}
.tree-row--active {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
}
.tree-row__main {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 1;
  min-width: 0;
  padding: 3px 4px 3px 8px;
  border: none;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}
.tree-row--active .tree-row__main,
.tree-row--active .tree-row__icon {
  color: rgb(var(--v-theme-primary));
}
.tree-row__icon {
  flex: none;
  color: var(--muted);
}
.tree-row__name {
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tree-row__dot {
  flex: none;
  width: 6px;
  height: 6px;
  margin-left: auto;
  border-radius: 50%;
  background: var(--warn);
}
/* The ⋯ shows on hover and focus; a right click opens the same menu. */
.tree-row__more {
  opacity: 0;
}
.tree-row:hover .tree-row__more,
.tree-row__more:focus-visible {
  opacity: 1;
}
@media (hover: none) {
  .tree-row__more {
    opacity: 1;
  }
}
.task-tree__anchor {
  position: absolute;
  width: 0;
  height: 0;
}
</style>
