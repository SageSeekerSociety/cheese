<script setup lang="ts">
// The wiring shell of a task's「文件」panel: it hands the panel's data
// (`composables/useTaskFiles`) to the scene, which only draws
// (`panels/PanelTaskFiles.vue`). It stands outside `components/panels/` for
// the reason `PanelChangesHost` gives.
//
// On the task page the files are the page's, held once by the work panel and
// given here (`files`, `tree`, `language`): the 编辑器 form draws the same tree
// in a column of its own, and a file open in one form is the same open file in
// the other. Given no `files` (a cell holding one file, opened beside the 文件
// panel, with its `path`; a panel mounted on its own) it holds its own, and the
// task's language servers (`composables/useTaskLanguage`, shared by every cell of
// the task) are reached from here.
import type { TaskFilesBundle } from '../../composables/useTaskFiles'
import type { TaskFileTreeState } from '../../composables/useTaskFileTree'
import type { LanguageLink } from '../../lib/lsp/link'

import { watch } from 'vue'

import { useTaskFiles } from '../../composables/useTaskFiles'
import { useTaskFileTree } from '../../composables/useTaskFileTree'
import { useTaskLanguage } from '../../composables/useTaskLanguage'
import PanelTaskFiles from '../panels/PanelTaskFiles.vue'

const props = withDefaults(
  defineProps<{
    projectId: string | null
    taskId: string | null
    readOnly?: boolean
    /** The panel is on screen. */
    active?: boolean
    /** Bumped when a turn ends. */
    refreshTick?: number
    /** Files this task changed. */
    changed?: string[]
    phone?: boolean
    agentName?: string
    /** The one file of a cell opened beside the 文件 panel. */
    path?: string | null
    /** The task's files, held by the page; without them this holds its own. */
    files?: TaskFilesBundle | null
    /** With `files`: which folders of the tree are open, or null when the tree is drawn elsewhere. */
    tree?: TaskFileTreeState | null
    /** With `files`: the task's language servers. */
    language?: LanguageLink | null
    /** One file at a time, no tab strip: the 对话 form's 文件 block, whose title bar says the path. */
    light?: boolean
    /** The tabs are drawn around it: the 编辑器 form's centre. */
    framed?: boolean
  }>(),
  {
    readOnly: false,
    active: false,
    refreshTick: 0,
    changed: () => [],
    phone: false,
    agentName: '',
    path: null,
    files: null,
    tree: null,
    language: null,
    light: false,
    framed: false,
  }
)

const emit = defineEmits<{ 'open-beside': [path: string] }>()

// Whether the files are given or held here is decided once, when the cell is made.
const given = props.files
const files =
  given ??
  useTaskFiles({
    projectId: () => props.projectId,
    taskId: () => props.taskId,
    readOnly: () => props.readOnly,
    active: () => props.active,
    refreshTick: () => props.refreshTick,
    // A cell holding one file keeps no tabs.
    tabs: () => !props.path,
  })
const ownTree = given || props.path ? null : useTaskFileTree(files)
// Go to definition in another file of the checkout opens it in this cell, at that line.
const ownLanguage = given
  ? null
  : useTaskLanguage({
      taskId: () => props.taskId,
      machine: () => files.environment.state.value,
      open: (path, line) => void files.open(path, { start: line, end: line }),
    }).link

watch(
  () => props.path,
  (path) => {
    if (path) void files.open(path)
  },
  { immediate: true }
)

// A file link in the conversation opens here when the task's copy has it, at
// the lines it points at.
defineExpose({
  openFile: (path: string, lines: { start: number; end: number } | null = null) => files.openFile(path, lines),
})
</script>

<template>
  <PanelTaskFiles
    :files="files"
    :changed="props.changed"
    :read-only="props.readOnly"
    :phone="props.phone"
    :agent-name="props.agentName"
    :single="!!props.path"
    :light="props.light"
    :framed="props.framed"
    :tree="given ? props.tree : ownTree"
    :language="given ? props.language : ownLanguage ?? null"
    @open-beside="emit('open-beside', $event)"
  />
</template>
