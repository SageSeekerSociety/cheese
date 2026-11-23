<script setup lang="ts">
// The left column of the task page's 编辑器 form: where the reader finds something to
// open. One row at its top: the switch between its views at the left — 文件 (the task's
// file tree), 搜索 (the text of the task's files) and 改动 (the files this task changed,
// with the lines added and removed; one opens that file's changes in the centre) — and,
// for 文件, the tree's actions at the right: 新建文件, and a ⋯ with 新建文件夹, 按名称找文件
// and 全部收起; for 改动, the same ⋯ as the 改动 panel's (which version to look at,
// 刷新). Which view shows is the page's (`view`), so it stays the same while the
// form is switched away and back. The search result last opened from 搜索 stays marked.
//
// It draws what the page holds — the task's files (`useTaskFiles`), which folders
// are open (`useTaskFileTree`), the search (`useTaskSearch`), the changed files and
// their diffs — and fetches nothing.
import type { TaskFilesBundle } from '../../composables/useTaskFiles'
import type { TaskFileTreeState } from '../../composables/useTaskFileTree'
import type { TaskSearchBundle } from '../../composables/useTaskSearch'
import type { FileSource, RoomTask } from '../../cx_types'
import type { FileDiff } from '../../lib/diff'
import type { MenuAction } from '../common/menuAction'

import { computed, ref, watch } from 'vue'

import AdaptiveMenu from '../common/AdaptiveMenu.vue'
import FileKindMark from '../common/FileKindMark.vue'

import ChangesMoreMenu from './ChangesMoreMenu.vue'
import ChangesOpenFile from './ChangesOpenFile.vue'
import { CODE_TAB, roomTabs } from './panelTabList'
import TaskFileBrowser from './TaskFileBrowser.vue'
import TaskFileSearch from './TaskFileSearch.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

export type NavigatorView = 'files' | 'search' | 'changes'

const props = withDefaults(
  defineProps<{
    files: TaskFilesBundle
    tree: TaskFileTreeState
    /** The search of the task's files (the 搜索 view). */
    search: TaskSearchBundle
    view: NavigatorView
    /** Bumped when the page asks for the 搜索 box (its shortcut): the box takes the focus. */
    searchFocusTick?: number
    /** Files this task changed (the work summary's list). */
    changed?: string[]
    /** Each changed file's diff, once read: the lines added and removed. */
    diffs?: FileDiff[]
    /** The changed file whose changes are open in the centre. */
    activeChange?: string | null
    /** The person may not change this task's files. */
    readOnly?: boolean
    /** Which version of the changes is shown, as the 改动 panel has it: its ⋯ in the row. */
    version?: { currentTask: RoomTask | undefined; fileSource: FileSource; refreshing: boolean } | null
  }>(),
  { changed: () => [], diffs: () => [], activeChange: null, readOnly: false, searchFocusTick: 0, version: null }
)

const emit = defineEmits<{
  'update:view': [view: NavigatorView]
  /** Open this file in the centre. */
  'open-file': [path: string]
  /** Open this file in the centre at this line (a search match). */
  'open-line': [path: string, line: number]
  /** Open this file in a cell of its own beside the centre. */
  'open-beside': [path: string]
  /** Open this changed file's changes in the centre. */
  'open-change': [path: string]
  'select-version': [source: FileSource]
  refresh: []
}>()

const changesTab = roomTabs({ withChat: false, review: false, task: true }).find((tab) => tab.key === 'changes')!
const VIEWS = computed(() => [
  { key: 'files' as const, label: CODE_TAB.label, title: CODE_TAB.label, icon: CODE_TAB.icon, count: 0 },
  {
    key: 'search' as const,
    label: t('work.room.navigator.search.label'),
    title: t('work.room.navigator.search.title'),
    icon: 'mdi-magnify',
    count: 0,
  },
  {
    key: 'changes' as const,
    label: changesTab.label,
    title: changesTab.label,
    icon: changesTab.icon,
    count: props.changed.length,
  },
])

// ---- the 搜索 box takes the focus when the person asked for it ----
// Asked before this column was first drawn (the shortcut also brings the column up): asked all the same.
const searchFocus = ref(props.view === 'search' && props.searchFocusTick > 0)
/** The task's environment, as the search waiting for it shows it. */
const searchEnvironment = computed(() => {
  const env = props.search.environment
  const state = env.state.value
  if (state !== 'preparing' && state !== 'failed' && state !== 'unanswered') return null
  return { state, since: env.since.value, detail: env.detail.value }
})
function pickView(key: NavigatorView) {
  if (key === 'search') searchFocus.value = true
  emit('update:view', key)
}
watch(
  () => props.searchFocusTick,
  () => (searchFocus.value = true)
)

// ---- the 文件 view's actions, in the column's row ----
const browser = ref<InstanceType<typeof TaskFileBrowser> | null>(null)
const moreOpen = ref(false)
const moreActions = computed<MenuAction[]>(() => [
  ...(props.readOnly
    ? []
    : [
        {
          key: 'dir',
          label: t('work.room.taskFiles.newFolder'),
          icon: 'mdi-folder-plus-outline',
          onSelect: () => browser.value?.askCreate('dir', ''),
        },
      ]),
  {
    key: 'find',
    label: t('work.room.taskFiles.find'),
    icon: 'mdi-file-search-outline',
    onSelect: () => (finding.value = true),
  },
  {
    key: 'collapse',
    label: t('work.room.taskFiles.collapseAll'),
    icon: 'mdi-collapse-all-outline',
    onSelect: () => props.tree.collapseAll(),
  },
])

// The changed files: from their diffs once read (with the lines added and removed), from
// the summary's list until then.
const rows = computed(() => {
  const list: { path: string; added?: number; removed?: number }[] = props.diffs.length
    ? props.diffs
    : props.changed.map((path) => ({ path }))
  return [...list]
    .sort((a, b) => a.path.localeCompare(b.path))
    .map((row) => {
      const at = row.path.lastIndexOf('/')
      return { ...row, name: row.path.slice(at + 1), folder: at < 0 ? '' : row.path.slice(0, at) }
    })
})

// ---- the search result last opened ----
const opened = ref<{ path: string; line: number } | null>(null)
function openLine(path: string, line: number) {
  opened.value = { path, line }
  emit('open-line', path, line)
}

const finding = ref(false)
function pickFound(path: string) {
  finding.value = false
  emit('open-file', path)
}
</script>

<template>
  <div class="task-nav">
    <div class="task-nav__head">
      <div class="task-nav__views" role="group" :aria-label="t('work.room.navigator.views')">
        <span v-for="v in VIEWS" :key="v.key" class="task-nav__view">
          <BaseButton
            kind="ghost"
            size="sm"
            :icon="v.icon"
            :class="{ 'task-nav__view--on': props.view === v.key }"
            :aria-label="v.label"
            :title="v.title"
            :aria-pressed="props.view === v.key ? 'true' : 'false'"
            @click="pickView(v.key)"
          />
          <span v-if="v.count" class="task-nav__count" aria-hidden="true">{{ v.count }}</span>
        </span>
      </div>
      <div v-if="props.view === 'files'" class="task-nav__actions">
        <BaseButton
          v-if="!props.readOnly"
          kind="ghost"
          size="sm"
          icon="mdi-file-plus-outline"
          :aria-label="t('work.room.taskFiles.newFile')"
          :title="t('work.room.taskFiles.newFile')"
          @click="browser?.askCreate('file', '')"
        />
        <AdaptiveMenu v-model="moreOpen" :actions="moreActions" :title="CODE_TAB.label">
          <template #activator="{ props: activator }">
            <BaseButton
              v-bind="activator"
              kind="ghost"
              size="sm"
              icon="mdi-dots-horizontal"
              :aria-label="t('work.room.navigator.more')"
              :title="t('work.room.navigator.more')"
            />
          </template>
        </AdaptiveMenu>
      </div>
      <div v-else-if="props.view === 'changes' && props.version" class="task-nav__actions">
        <ChangesMoreMenu
          :current-task="props.version.currentTask"
          :file-source="props.version.fileSource"
          :can-download="false"
          :refreshing="props.version.refreshing"
          @select-version="emit('select-version', $event)"
          @refresh="emit('refresh')"
        />
      </div>
    </div>
    <TaskFileBrowser
      v-if="props.view === 'files'"
      ref="browser"
      class="task-nav__body"
      :files="props.files"
      :tree="props.tree"
      :changed="props.changed"
      :editable="!props.readOnly"
      :head="false"
      @select="emit('open-file', $event)"
      @open-beside="emit('open-beside', $event)"
      @find="finding = true"
    />
    <TaskFileSearch
      v-else-if="props.view === 'search'"
      class="task-nav__body"
      :query="props.search.query.value"
      :state="props.search.state.value"
      :result="props.search.result.value"
      :error="props.search.error.value"
      :final="props.search.final.value"
      :environment="searchEnvironment"
      :focus="searchFocus"
      :opened="opened"
      @update:query="props.search.setQuery"
      @submit="props.search.searchNow()"
      @retry="props.search.searchNow()"
      @open="openLine"
      @focused="searchFocus = false"
    />
    <template v-else>
      <p v-if="!rows.length" class="task-nav__empty">{{ t('work.room.changes.noChanges') }}</p>
      <ul v-else class="task-nav__list" :aria-label="changesTab.label">
        <li v-for="row in rows" :key="row.path">
          <button
            type="button"
            class="change-row"
            :class="{ 'change-row--active': row.path === props.activeChange }"
            :aria-current="row.path === props.activeChange ? 'true' : undefined"
            :title="row.path"
            @click="emit('open-change', row.path)"
          >
            <FileKindMark :name="row.name" />
            <span class="change-row__name">{{ row.name }}</span>
            <span v-if="row.folder" class="change-row__folder">{{ row.folder }}</span>
            <span v-if="row.added !== undefined" class="change-row__counts">
              <span class="change-row__added" :aria-label="t('work.room.navigator.added', { count: row.added })"
                >+{{ row.added }}</span
              >
              <span class="change-row__removed" :aria-label="t('work.room.navigator.removed', { count: row.removed })"
                >−{{ row.removed }}</span
              >
            </span>
          </button>
        </li>
      </ul>
    </template>
    <ChangesOpenFile v-model="finding" :files="props.files.files.value" @pick="pickFound" />
  </div>
</template>

<style scoped>
.task-nav {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  background: var(--canvas);
}
.task-nav__body {
  flex: 1 1 auto;
  min-height: 0;
}
/* The switch between the views, a row of its own above whichever view shows. */
.task-nav__head {
  display: flex;
  flex: none;
  align-items: center;
  height: 34px;
  padding: 0 4px;
  border-bottom: 1px solid var(--line);
}
.task-nav__views {
  display: flex;
  flex: 1 0 auto;
  gap: 2px;
  align-items: center;
  min-width: 0;
}
.task-nav__view {
  position: relative;
  display: inline-flex;
}
.task-nav__actions {
  display: flex;
  flex: none;
  gap: 2px;
  align-items: center;
  margin-left: auto;
}
.task-nav__view--on {
  background: var(--fill-2);
  color: var(--ink);
}
.task-nav__count {
  position: absolute;
  top: 0;
  right: -2px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  pointer-events: none;
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 16px;
  color: var(--accent-ink);
  text-align: center;
  background: var(--accent-wash);
  border-radius: var(--radius-pill);
}
.task-nav__empty {
  margin: 0;
  padding: 8px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.task-nav__list {
  flex: 1 1 auto;
  min-height: 0;
  margin: 0;
  padding: 4px 0 8px;
  overflow-y: auto;
  list-style: none;
}
.change-row {
  display: flex;
  gap: 6px;
  align-items: center;
  width: 100%;
  height: 24px;
  padding: 0 8px 0 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: none;
}
.change-row:hover {
  background: var(--fill-2);
}
.change-row:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.change-row--active,
.change-row--active:hover {
  color: var(--ink);
  background: var(--line-2);
}
.change-row__name {
  flex: none;
  max-width: 60%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.change-row__folder {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.change-row__counts {
  display: inline-flex;
  flex: none;
  gap: 6px;
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
}
.change-row__added {
  color: var(--ok-ink);
}
.change-row__removed {
  color: var(--danger-ink);
}
</style>
