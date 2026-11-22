<script setup lang="ts">
// The tree of a task's「文件」panel: its rows (`lib/taskFileTree`), a dot on
// each file this task changed, and the person's ways to change the tree —
// new file and new folder at the top, rename and delete on each row (its ⋯
// or a right click). The head also finds a file by name and folds every
// folder. It only says what was asked; the panel does it.
import type { TaskFileRow } from '../../lib/taskFileTree'
import type { MenuAction } from '../common/menuAction'

import { nextTick, ref, watch } from 'vue'

import AdaptiveMenu from '../common/AdaptiveMenu.vue'
import FileKindMark from '../common/FileKindMark.vue'

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
  /** Not a phone: the tree has its head row, and a file can be opened in a cell beside this one. */
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
  /** Find a file by name. */
  find: []
  /** Fold every folder. */
  'collapse-all': []
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
const hasMenu = (row: TaskFileRow) => props.editable || (!!props.canSplit && row.type === 'file')
</script>

<template>
  <div class="task-tree">
    <div v-if="props.canSplit" class="task-tree__head">
      <span class="task-tree__title">{{ t('work.room.tabs.files') }}</span>
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-magnify"
        :aria-label="t('work.room.taskFiles.find')"
        :title="t('work.room.taskFiles.findTitle')"
        @click="emit('find')"
      />
      <template v-if="props.editable">
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
      </template>
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-collapse-all-outline"
        :aria-label="t('work.room.taskFiles.collapseAll')"
        :title="t('work.room.taskFiles.collapseAll')"
        @click="emit('collapse-all')"
      />
    </div>
    <div ref="listEl" class="task-tree__list">
      <p v-if="!props.rows.length" class="task-tree__empty">{{ t('work.room.changes.noFiles') }}</p>
      <div
        v-for="row in props.rows"
        :key="`${row.type}:${row.path}`"
        class="tree-row"
        :class="{
          'tree-row--active': row.type === 'file' && row.path === props.activePath,
          'tree-row--menu': hasMenu(row),
        }"
      >
        <button
          type="button"
          class="tree-row__main"
          :aria-expanded="row.type === 'dir' ? props.open.has(row.path) : undefined"
          :aria-current="row.type === 'file' && row.path === props.activePath ? 'true' : undefined"
          :style="{ paddingLeft: `${8 + row.depth * 14 + (row.type === 'file' ? 20 : 0)}px` }"
          :title="row.path"
          @click="pick(row)"
          @contextmenu.prevent="showMenu(row, $event)"
        >
          <template v-if="row.type === 'dir'">
            <v-icon size="14" class="tree-row__chevron">
              {{ props.open.has(row.path) ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
            </v-icon>
            <v-icon size="16" class="tree-row__folder">
              {{ props.open.has(row.path) ? 'mdi-folder-open-outline' : 'mdi-folder-outline' }}
            </v-icon>
          </template>
          <FileKindMark v-else :name="row.name" />
          <span class="tree-row__name">{{ row.name }}</span>
          <span
            v-if="row.type === 'file' && props.changed.has(row.path)"
            class="tree-row__dot"
            :aria-label="t('work.room.taskFiles.changed')"
          />
        </button>
        <button
          v-if="hasMenu(row)"
          type="button"
          class="tree-row__more"
          :aria-label="t('project.artifacts.actionsOf', { name: row.name })"
          :title="t('project.artifacts.actionsOf', { name: row.name })"
          @click="showMenu(row)"
        >
          <v-icon size="16">mdi-dots-horizontal</v-icon>
        </button>
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
}
.task-tree__head {
  display: flex;
  flex: none;
  align-items: center;
  gap: 2px;
  height: 34px;
  padding: 0 4px 0 12px;
  border-bottom: 1px solid var(--line);
}
.task-tree__title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
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
  height: 24px;
}
.tree-row:hover {
  background: var(--fill-2);
}
.tree-row--active,
.tree-row--active:hover {
  background: var(--line-2);
}
.tree-row__main {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 100%;
  padding: 0 8px;
  border: none;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}
.tree-row--active .tree-row__main {
  color: var(--ink);
}
.tree-row__main:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.tree-row__chevron {
  flex: none;
  color: var(--faint);
}
.tree-row__folder {
  flex: none;
  color: var(--muted);
}
.tree-row__name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tree-row__dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--warn);
}
/* The ⋯ lies over the row's right end on hover and focus, taking no width at
   rest; a right click opens the same menu. */
.tree-row__more {
  position: absolute;
  top: 1px;
  right: 4px;
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  color: var(--muted);
  cursor: pointer;
  opacity: 0;
  pointer-events: none;
}
.tree-row--active .tree-row__more {
  background: var(--line-2);
}
.tree-row:hover .tree-row__more,
.tree-row__more:focus-visible {
  opacity: 1;
  pointer-events: auto;
}
.tree-row__more:hover {
  color: var(--ink);
}
.tree-row__more:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.tree-row--menu:hover .tree-row__dot {
  visibility: hidden;
}
/* No hover on touch: the ⋯ stays, and the name leaves it room. */
@media (hover: none) {
  .tree-row__more {
    background: transparent;
    opacity: 1;
    pointer-events: auto;
  }
  .tree-row--menu .tree-row__main {
    padding-right: 30px;
  }
  .tree-row--menu:hover .tree-row__dot {
    visibility: visible;
  }
}
.task-tree__anchor {
  position: absolute;
  width: 0;
  height: 0;
}
</style>
